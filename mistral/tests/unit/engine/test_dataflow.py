# Copyright 2014 - Mirantis, Inc.
# Copyright 2015 - StackStorm, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import mock

from oslo_config import cfg

from mistral.db.v2 import api as db_api
from mistral.db.v2.sqlalchemy import models
from mistral.services import workflows as wf_service
from mistral.tests.unit import base as test_base
from mistral.tests.unit.engine import base as engine_test_base
from mistral.workflow import data_flow
from mistral.workflow import states


# Use the set_default method to set value otherwise in certain test cases
# the change in value is not permanent.
cfg.CONF.set_default('auth_enable', False, group='pecan')


class DataFlowEngineTest(engine_test_base.EngineTestCase):
    def test_linear_dataflow(self):
        linear_wf = """---
        version: '2.0'

        wf:
          type: direct

          tasks:
            task1:
              action: std.echo output="Hi"
              publish:
                hi: <% $.task1 %>
              on-success:
                - task2

            task2:
              action: std.echo output="Morpheus"
              publish:
                to: <% $.task2 %>
              on-success:
                - task3

            task3:
              publish:
                result: "<% $.hi %>, <% $.to %>! Your <% env().from %>."
        """

        wf_service.create_workflows(linear_wf)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {}, env={'from': 'Neo'})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        self.assertEqual(states.SUCCESS, wf_ex.state)

        tasks = wf_ex.task_executions

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task3 = self._assert_single_item(tasks, name='task3')

        self.assertEqual(states.SUCCESS, task3.state)
        self.assertDictEqual({'hi': 'Hi'}, task1.published)
        self.assertDictEqual({'to': 'Morpheus'}, task2.published)
        self.assertDictEqual(
            {'result': 'Hi, Morpheus! Your Neo.'},
            task3.published
        )

    def test_linear_with_branches_dataflow(self):
        linear_with_branches_wf = """---
        version: '2.0'

        wf:
          type: direct

          tasks:
            task1:
              action: std.echo output="Hi"
              publish:
                hi: <% $.task1 %>
                progress: "completed task1"
              on-success:
                - notify
                - task2

            task2:
              action: std.echo output="Morpheus"
              publish:
                to: <% $.task2 %>
                progress: "completed task2"
              on-success:
                - notify
                - task3

            task3:
              publish:
                result: "<% $.hi %>, <% $.to %>! Your <% env().from %>."
                progress: "completed task3"
              on-success:
                - notify

            notify:
              action: std.echo output=<% $.progress %>
              publish:
                progress: <% $.notify %>
        """

        wf_service.create_workflows(linear_with_branches_wf)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {}, env={'from': 'Neo'})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        self.assertEqual(states.SUCCESS, wf_ex.state)

        tasks = wf_ex.task_executions

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task3 = self._assert_single_item(tasks, name='task3')

        notify_tasks = self._assert_multiple_items(tasks, 3, name='notify')
        notify_published_arr = [t.published['progress'] for t in notify_tasks]

        self.assertEqual(states.SUCCESS, task3.state)

        exp_published_arr = [
            {
                'hi': 'Hi',
                'progress': 'completed task1'
            },
            {
                'to': 'Morpheus',
                'progress': 'completed task2'
            },
            {
                'result': 'Hi, Morpheus! Your Neo.',
                'progress': 'completed task3'
            }
        ]

        self.assertDictEqual(exp_published_arr[0], task1.published)
        self.assertDictEqual(exp_published_arr[1], task2.published)
        self.assertDictEqual(exp_published_arr[2], task3.published)
        self.assertIn(exp_published_arr[0]['progress'], notify_published_arr)
        self.assertIn(exp_published_arr[1]['progress'], notify_published_arr)
        self.assertIn(exp_published_arr[2]['progress'], notify_published_arr)

    def test_parallel_tasks(self):
        parallel_tasks_wf = """---
        version: '2.0'

        wf:
          type: direct

          tasks:
            task1:
              action: std.echo output=1
              publish:
                var1: <% $.task1 %>

            task2:
              action: std.echo output=2
              publish:
                var2: <% $.task2 %>
        """

        wf_service.create_workflows(parallel_tasks_wf)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        self.assertEqual(states.SUCCESS, wf_ex.state)

        tasks = wf_ex.task_executions

        self.assertEqual(2, len(tasks))

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')

        self.assertEqual(states.SUCCESS, task1.state)
        self.assertEqual(states.SUCCESS, task2.state)

        self.assertDictEqual({'var1': 1}, task1.published)
        self.assertDictEqual({'var2': 2}, task2.published)

        self.assertEqual(1, wf_ex.output['var1'])
        self.assertEqual(2, wf_ex.output['var2'])

    def test_parallel_tasks_complex(self):
        parallel_tasks_complex_wf = """---
        version: '2.0'

        wf:
          type: direct

          tasks:
            task1:
              action: std.noop
              publish:
                var1: 1
              on-complete:
                - task12

            task12:
              action: std.noop
              publish:
                var12: 12
              on-complete:
                - task13
                - task14

            task13:
              action: std.fail
              description: |
                Since this task fails we expect that 'var13' won't go into
                context. Only 'var14'.
              publish:
                var13: 13
              on-error:
                - noop

            task14:
              publish:
                var14: 14

            task2:
              publish:
                var2: 2
              on-complete:
                - task21

            task21:
              publish:
                var21: 21
        """

        wf_service.create_workflows(parallel_tasks_complex_wf)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        self.assertEqual(states.SUCCESS, wf_ex.state)

        tasks = wf_ex.task_executions

        self.assertEqual(6, len(tasks))

        task1 = self._assert_single_item(tasks, name='task1')
        task12 = self._assert_single_item(tasks, name='task12')
        task13 = self._assert_single_item(tasks, name='task13')
        task14 = self._assert_single_item(tasks, name='task14')
        task2 = self._assert_single_item(tasks, name='task2')
        task21 = self._assert_single_item(tasks, name='task21')

        self.assertEqual(states.SUCCESS, task1.state)
        self.assertEqual(states.SUCCESS, task12.state)
        self.assertEqual(states.ERROR, task13.state)
        self.assertEqual(states.SUCCESS, task14.state)
        self.assertEqual(states.SUCCESS, task2.state)
        self.assertEqual(states.SUCCESS, task21.state)

        self.assertDictEqual({'var1': 1}, task1.published)
        self.assertDictEqual({'var12': 12}, task12.published)
        self.assertDictEqual({'var14': 14}, task14.published)
        self.assertDictEqual({'var2': 2}, task2.published)
        self.assertDictEqual({'var21': 21}, task21.published)

        self.assertEqual(1, wf_ex.output['var1'])
        self.assertEqual(12, wf_ex.output['var12'])
        self.assertFalse('var13' in wf_ex.output)
        self.assertEqual(14, wf_ex.output['var14'])
        self.assertEqual(2, wf_ex.output['var2'])
        self.assertEqual(21, wf_ex.output['var21'])

    def test_sequential_tasks_publishing_same_var(self):
        var_overwrite_wf = """---
        version: '2.0'

        wf:
          type: direct

          tasks:
            task1:
              action: std.echo output="Hi"
              publish:
                greeting: <% $.task1 %>
              on-success:
                - task2

            task2:
              action: std.echo output="Yo"
              publish:
                greeting: <% $.task2 %>
              on-success:
                - task3

            task3:
              action: std.echo output="Morpheus"
              publish:
                to: <% $.task3 %>
              on-success:
                - task4

            task4:
              publish:
                result: "<% $.greeting %>, <% $.to %>! <% env().from %>."
        """

        wf_service.create_workflows(var_overwrite_wf)

        # Start workflow.
        wf_ex = self.engine.start_workflow(
            'wf',
            {},
            env={'from': 'Neo'}
        )

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        self.assertEqual(states.SUCCESS, wf_ex.state)

        tasks = wf_ex.task_executions

        task1 = self._assert_single_item(tasks, name='task1')
        task2 = self._assert_single_item(tasks, name='task2')
        task3 = self._assert_single_item(tasks, name='task3')
        task4 = self._assert_single_item(tasks, name='task4')

        self.assertEqual(states.SUCCESS, task4.state)
        self.assertDictEqual({'greeting': 'Hi'}, task1.published)
        self.assertDictEqual({'greeting': 'Yo'}, task2.published)
        self.assertDictEqual({'to': 'Morpheus'}, task3.published)
        self.assertDictEqual(
            {'result': 'Yo, Morpheus! Neo.'},
            task4.published
        )

    def test_linear_dataflow_implicit_publish(self):
        linear_wf = """---
        version: '2.0'

        wf:
          type: direct

          tasks:
            task1:
              action: std.echo output="Hi"
              on-success:
                - task21
                - task22

            task21:
              action: std.echo output="Morpheus"
              on-success:
                - task4

            task22:
              action: std.echo output="Neo"
              on-success:
                - task4

            task4:
              join: all
              publish:
                result: "<% $.task1 %>, <% $.task21 %>! Your <% $.task22 %>."
        """
        wf_service.create_workflows(linear_wf)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions
        task4 = self._assert_single_item(tasks, name='task4')

        self.assertDictEqual(
            {'result': 'Hi, Morpheus! Your Neo.'},
            task4.published
        )

    def test_destroy_result(self):
        linear_wf = """---
        version: '2.0'

        wf:
          type: direct

          tasks:
            task1:
              action: std.echo output=["Hi", "John Doe!"]
              publish:
                hi: <% $.task1 %>
              keep-result: false

        """
        wf_service.create_workflows(linear_wf)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions
        task1 = self._assert_single_item(tasks, name='task1')

        result = data_flow.get_task_execution_result(task1)

        # Published vars are saved.
        self.assertDictEqual(
            {'hi': ["Hi", "John Doe!"]},
            task1.published
        )

        # But all result is cleared.
        self.assertIsNone(result)

    def test_empty_with_items(self):
        wf = """---
        version: "2.0"

        wf1_with_items:
           type: direct

           tasks:
             task1:
               with-items: i in <% list() %>
               action: std.echo output= "Task 1.<% $.i %>"
               publish:
                 result: <% $.task1 %>
        """
        wf_service.create_workflows(wf)

        # Start workflow.
        wf_ex = self.engine.start_workflow('wf1_with_items', {})

        self._await(lambda: self.is_execution_success(wf_ex.id))

        # Note: We need to reread execution to access related tasks.
        wf_ex = db_api.get_workflow_execution(wf_ex.id)

        tasks = wf_ex.task_executions
        task1 = self._assert_single_item(tasks, name='task1')

        result = data_flow.get_task_execution_result(task1)
        self.assertListEqual([], result)


class DataFlowTest(test_base.BaseTest):
    def test_get_task_execution_result(self):
        task_ex = models.TaskExecution(
            name='task1',
            spec={
                "version": '2.0',
                'name': 'task1',
                'with-items': 'var in [1]',
                'type': 'direct'
            },
            runtime_context={
                'with_items_context': {'count': 1}
            }
        )

        action_exs = []

        action_exs.append(models.ActionExecution(
            name='my_action',
            output={'result': 1},
            accepted=True,
            runtime_context={'with_items_index': 0}
        ))

        with mock.patch.object(db_api, 'get_action_executions',
                               return_value=action_exs):
            self.assertEqual([1], data_flow.get_task_execution_result(task_ex))

        action_exs.append(models.ActionExecution(
            name='my_action',
            output={'result': 1},
            accepted=True,
            runtime_context={'with_items_index': 0}
        ))

        action_exs.append(models.ActionExecution(
            name='my_action',
            output={'result': 1},
            accepted=False,
            runtime_context={'with_items_index': 0}
        ))

        with mock.patch.object(db_api, 'get_action_executions',
                               return_value=action_exs):
            self.assertEqual(
                [1, 1],
                data_flow.get_task_execution_result(task_ex)
            )
