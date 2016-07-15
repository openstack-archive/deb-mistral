# Copyright 2016 - Nokia Networks.
# Copyright 2016 - Brocade Communications Systems, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import abc
import copy
from oslo_config import cfg
from oslo_log import log as logging
from osprofiler import profiler
import six

from mistral.db.v2 import api as db_api
from mistral.db.v2.sqlalchemy import models as db_models
from mistral.engine import dispatcher
from mistral.engine.rpc import rpc
from mistral.engine import utils as eng_utils
from mistral import exceptions as exc
from mistral.services import scheduler
from mistral.services import workflows as wf_service
from mistral import utils
from mistral.utils import wf_trace
from mistral.workbook import parser as spec_parser
from mistral.workflow import base as wf_base
from mistral.workflow import commands
from mistral.workflow import data_flow
from mistral.workflow import states
from mistral.workflow import utils as wf_utils


LOG = logging.getLogger(__name__)

_SEND_RESULT_TO_PARENT_WORKFLOW_PATH = (
    'mistral.engine.workflows._send_result_to_parent_workflow'
)


@six.add_metaclass(abc.ABCMeta)
class Workflow(object):
    """Workflow.

    Represents a workflow and defines interface that can be used by
    Mistral engine or its components in order to manipulate with workflows.
    """

    def __init__(self, wf_def, wf_ex=None):
        self.wf_def = wf_def
        self.wf_ex = wf_ex
        self.wf_spec = spec_parser.get_workflow_spec(wf_def.spec)

    @profiler.trace('workflow-start')
    def start(self, input_dict, desc='', params=None):
        """Start workflow.

        :param input_dict: Workflow input.
        :param desc: Workflow execution description.
        :param params: Workflow type specific parameters.
        """

        assert not self.wf_ex

        wf_trace.info(self.wf_ex, "Starting workflow: %s" % self.wf_def)

        # TODO(rakhmerov): This call implicitly changes input_dict! Fix it!
        # After fix we need to move validation after adding risky fields.
        eng_utils.validate_input(self.wf_def, input_dict, self.wf_spec)

        self._create_execution(input_dict, desc, params)

        self.set_state(states.RUNNING)

        wf_ctrl = wf_base.get_controller(self.wf_ex, self.wf_spec)

        cmds = wf_ctrl.continue_workflow()

        dispatcher.dispatch_workflow_commands(self.wf_ex, cmds)

    def stop(self, state, msg=None):
        """Stop workflow.

        :param state: New workflow state.
        :param msg: Additional explaining message.
        """

        assert self.wf_ex

        if state == states.SUCCESS:
            wf_ctrl = wf_base.get_controller(self.wf_ex)

            final_context = {}

            try:
                final_context = wf_ctrl.evaluate_workflow_final_context()
            except Exception as e:
                LOG.warning(
                    'Failed to get final context for %s: %s' % (self.wf_ex, e)
                )

            return self._succeed_workflow(final_context, msg)
        elif state == states.ERROR:
            return self._fail_workflow(msg)

    @profiler.trace('workflow-on-task-complete')
    def on_task_complete(self, task_ex):
        """Handle task completion event.

        :param task_ex: Task execution that's completed.
        """

        assert self.wf_ex

        self._check_and_complete()

    def resume(self, env=None):
        """Resume workflow.

        :param env: Environment.
        """

        assert self.wf_ex

        wf_service.update_workflow_execution_env(self.wf_ex, env)

        self.set_state(states.RUNNING, recursive=True)

        self._continue_workflow(env=env)

    def rerun(self, task_ex, reset=True, env=None):
        """Rerun workflow from the given task.

        :param task_ex: Task execution that the workflow needs to rerun from.
        :param reset: If True, reset task state including deleting its action
            executions.
        :param env: Environment.
        """

        assert self.wf_ex

        wf_service.update_workflow_execution_env(self.wf_ex, env)

        self.set_state(states.RUNNING, recursive=True)

        self._continue_workflow(task_ex, reset, env=env)

    @profiler.trace('workflow-lock')
    def lock(self):
        assert self.wf_ex

        return db_api.acquire_lock(db_models.WorkflowExecution, self.wf_ex.id)

    def _create_execution(self, input_dict, desc, params):
        self.wf_ex = db_api.create_workflow_execution({
            'name': self.wf_def.name,
            'description': desc,
            'workflow_name': self.wf_def.name,
            'workflow_id': self.wf_def.id,
            'spec': self.wf_spec.to_dict(),
            'state': states.IDLE,
            'output': {},
            'task_execution_id': params.get('task_execution_id'),
            'runtime_context': {
                'index': params.get('index', 0)
            },
        })

        self.wf_ex.input = input_dict or {}
        self.wf_ex.context = copy.deepcopy(input_dict) or {}

        env = _get_environment(params)

        if env:
            params['env'] = env

        self.wf_ex.params = params

        data_flow.add_openstack_data_to_context(self.wf_ex)
        data_flow.add_execution_to_context(self.wf_ex)
        data_flow.add_environment_to_context(self.wf_ex)
        data_flow.add_workflow_variables_to_context(self.wf_ex, self.wf_spec)

    @profiler.trace('workflow-set-state')
    def set_state(self, state, state_info=None, recursive=False):
        assert self.wf_ex

        cur_state = self.wf_ex.state

        if states.is_valid_transition(cur_state, state):
            self.wf_ex.state = state
            self.wf_ex.state_info = state_info

            wf_trace.info(
                self.wf_ex,
                "Execution of workflow '%s' [%s -> %s]"
                % (self.wf_ex.workflow_name, cur_state, state)
            )
        else:
            msg = ("Can't change workflow execution state from %s to %s. "
                   "[workflow=%s, execution_id=%s]" %
                   (cur_state, state, self.wf_ex.name, self.wf_ex.id))

            raise exc.WorkflowException(msg)

        # Workflow result should be accepted by parent workflows (if any)
        # only if it completed successfully or failed.
        self.wf_ex.accepted = state in (states.SUCCESS, states.ERROR)

        if recursive and self.wf_ex.task_execution_id:
            parent_task_ex = db_api.get_task_execution(
                self.wf_ex.task_execution_id
            )

            parent_wf = Workflow(
                db_api.get_workflow_definition(parent_task_ex.workflow_id),
                parent_task_ex.workflow_execution
            )

            parent_wf.lock()

            parent_wf.set_state(state, recursive=recursive)

            # TODO(rakhmerov): It'd be better to use instance of Task here.
            parent_task_ex.state = state
            parent_task_ex.state_info = None
            parent_task_ex.processed = False

    def _continue_workflow(self, task_ex=None, reset=True, env=None):
        wf_ctrl = wf_base.get_controller(self.wf_ex)

        # Calculate commands to process next.
        cmds = wf_ctrl.continue_workflow(task_ex=task_ex, reset=reset, env=env)

        # When resuming a workflow we need to ignore all 'pause'
        # commands because workflow controller takes tasks that
        # completed within the period when the workflow was paused.
        cmds = list(
            filter(lambda c: not isinstance(c, commands.PauseWorkflow), cmds)
        )

        # Since there's no explicit task causing the operation
        # we need to mark all not processed tasks as processed
        # because workflow controller takes only completed tasks
        # with flag 'processed' equal to False.
        for t_ex in self.wf_ex.task_executions:
            if states.is_completed(t_ex.state) and not t_ex.processed:
                t_ex.processed = True

        dispatcher.dispatch_workflow_commands(self.wf_ex, cmds)

        if not cmds:
            self._check_and_complete()

    def _check_and_complete(self):
        if states.is_paused_or_completed(self.wf_ex.state):
            return

        # Workflow is not completed if there are any incomplete task
        # executions that are not in WAITING state. If all incomplete
        # tasks are waiting and there are unhandled errors, then these
        # tasks will not reach completion. In this case, mark the
        # workflow complete.
        incomplete_tasks = wf_utils.find_incomplete_task_executions(self.wf_ex)

        if any(not states.is_waiting(t.state) for t in incomplete_tasks):
            return

        wf_ctrl = wf_base.get_controller(self.wf_ex, self.wf_spec)

        if wf_ctrl.all_errors_handled():
            self._succeed_workflow(wf_ctrl.evaluate_workflow_final_context())
        else:
            self._fail_workflow(_build_fail_info_message(wf_ctrl, self.wf_ex))

    def _succeed_workflow(self, final_context, msg=None):
        self.wf_ex.output = data_flow.evaluate_workflow_output(
            self.wf_spec,
            final_context
        )

        # Set workflow execution to success until after output is evaluated.
        self.set_state(states.SUCCESS, msg)

        if self.wf_ex.task_execution_id:
            self._schedule_send_result_to_parent_workflow()

    def _fail_workflow(self, msg):
        if states.is_paused_or_completed(self.wf_ex.state):
            return

        self.set_state(states.ERROR, state_info=msg)

        # When we set an ERROR state we should safely set output value getting
        # w/o exceptions due to field size limitations.
        msg = utils.cut_by_kb(
            msg,
            cfg.CONF.engine.execution_field_size_limit_kb
        )

        self.wf_ex.output = {'result': msg}

        if self.wf_ex.task_execution_id:
            self._schedule_send_result_to_parent_workflow()

    def _schedule_send_result_to_parent_workflow(self):
        scheduler.schedule_call(
            None,
            _SEND_RESULT_TO_PARENT_WORKFLOW_PATH,
            0,
            wf_ex_id=self.wf_ex.id
        )


def _get_environment(params):
    env = params.get('env', {})

    if isinstance(env, dict):
        return env

    if isinstance(env, six.string_types):
        env_db = db_api.load_environment(env)

        if not env_db:
            raise exc.InputException(
                'Environment is not found: %s' % env
            )

        return env_db.variables

    raise exc.InputException(
        'Unexpected value type for environment [env=%s, type=%s]'
        % (env, type(env))
    )


def _send_result_to_parent_workflow(wf_ex_id):
    wf_ex = db_api.get_workflow_execution(wf_ex_id)

    if wf_ex.state == states.SUCCESS:
        rpc.get_engine_client().on_action_complete(
            wf_ex.id,
            wf_utils.Result(data=wf_ex.output)
        )
    elif wf_ex.state == states.ERROR:
        err_msg = (
            wf_ex.state_info or
            'Failed subworkflow [execution_id=%s]' % wf_ex.id
        )

        rpc.get_engine_client().on_action_complete(
            wf_ex.id,
            wf_utils.Result(error=err_msg)
        )


def _build_fail_info_message(wf_ctrl, wf_ex):
    # Try to find where error is exactly.
    failed_tasks = sorted(
        filter(
            lambda t: not wf_ctrl.is_error_handled_for(t),
            wf_utils.find_error_task_executions(wf_ex)
        ),
        key=lambda t: t.name
    )

    msg = ('Failure caused by error in tasks: %s\n' %
           ', '.join([t.name for t in failed_tasks]))

    for t in failed_tasks:
        msg += '\n  %s [task_ex_id=%s] -> %s\n' % (t.name, t.id, t.state_info)

        for i, ex in enumerate(t.executions):
            if ex.state == states.ERROR:
                output = (ex.output or dict()).get('result', 'Unknown')
                msg += (
                    '    [action_ex_id=%s, idx=%s]: %s\n' % (
                        ex.id,
                        i,
                        str(output)
                    )
                )

    return msg
