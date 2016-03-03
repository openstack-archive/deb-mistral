# Copyright 2015 - Mirantis, Inc.
# Copyright 2015 - StackStorm, Inc.
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

import hashlib
import json
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import backref
from sqlalchemy.orm import relationship
import sys

from oslo_config import cfg
from oslo_log import log as logging

from mistral.db.sqlalchemy import model_base as mb
from mistral.db.sqlalchemy import types as st
from mistral import exceptions as exc
from mistral.services import security
from mistral import utils


# Definition objects.

LOG = logging.getLogger(__name__)


class Definition(mb.MistralSecureModelBase):
    __abstract__ = True

    id = mb.id_column()
    name = sa.Column(sa.String(80))
    definition = sa.Column(sa.Text(), nullable=True)
    spec = sa.Column(st.JsonDictType())
    tags = sa.Column(st.JsonListType())
    is_system = sa.Column(sa.Boolean())


# There's no WorkbookExecution so we safely omit "Definition" in the name.
class Workbook(Definition):
    """Contains info about workbook (including definition in Mistral DSL)."""

    __tablename__ = 'workbooks_v2'

    __table_args__ = (
        sa.UniqueConstraint('name', 'project_id'),
        sa.Index('%s_project_id' % __tablename__, 'project_id'),
        sa.Index('%s_scope' % __tablename__, 'scope'),
    )


class WorkflowDefinition(Definition):
    """Contains info about workflow (including definition in Mistral DSL)."""

    __tablename__ = 'workflow_definitions_v2'

    __table_args__ = (
        sa.UniqueConstraint('name', 'project_id'),
        sa.Index('%s_is_system' % __tablename__, 'is_system'),
        sa.Index('%s_project_id' % __tablename__, 'project_id'),
        sa.Index('%s_scope' % __tablename__, 'scope'),
    )


class ActionDefinition(Definition):
    """Contains info about registered Actions."""

    __tablename__ = 'action_definitions_v2'

    __table_args__ = (
        sa.UniqueConstraint('name', 'project_id'),
        sa.Index('%s_is_system' % __tablename__, 'is_system'),
        sa.Index('%s_action_class' % __tablename__, 'action_class'),
        sa.Index('%s_project_id' % __tablename__, 'project_id'),
        sa.Index('%s_scope' % __tablename__, 'scope'),
    )

    # Main properties.
    description = sa.Column(sa.Text())
    input = sa.Column(sa.Text())

    # Service properties.
    action_class = sa.Column(sa.String(200))
    attributes = sa.Column(st.JsonDictType())


# Execution objects.

class Execution(mb.MistralSecureModelBase):
    """Abstract execution object."""

    __tablename__ = 'executions_v2'

    __table_args__ = (
        sa.Index('%s_project_id' % __tablename__, 'project_id'),
        sa.Index('%s_scope' % __tablename__, 'scope'),
        sa.Index('%s_state' % __tablename__, 'state'),
        sa.Index('%s_type' % __tablename__, 'type'),
        sa.Index('%s_updated_at' % __tablename__, 'updated_at'),
    )

    type = sa.Column(sa.String(50))

    __mapper_args__ = {
        'polymorphic_on': type,
        'polymorphic_identity': 'execution'
    }

    # Main properties.
    id = mb.id_column()
    name = sa.Column(sa.String(80))
    description = sa.Column(sa.String(255), nullable=True)
    workflow_name = sa.Column(sa.String(80))
    workflow_id = sa.Column(sa.String(80))
    spec = sa.Column(st.JsonDictType())
    state = sa.Column(sa.String(20))
    state_info = sa.Column(sa.Text(), nullable=True)
    tags = sa.Column(st.JsonListType())

    # Runtime context like iteration_no of a repeater.
    # Effectively internal engine properties which will be used to determine
    # execution of a task.
    runtime_context = sa.Column(st.JsonLongDictType())


class ActionExecution(Execution):
    """Contains action execution information."""

    __mapper_args__ = {
        'polymorphic_identity': 'action_execution'
    }

    # Main properties.
    accepted = sa.Column(sa.Boolean(), default=False)
    input = sa.Column(st.JsonLongDictType(), nullable=True)

    output = sa.orm.deferred(sa.Column(st.JsonLongDictType(), nullable=True))


class WorkflowExecution(ActionExecution):
    """Contains workflow execution information."""

    __mapper_args__ = {
        'polymorphic_identity': 'workflow_execution'
    }

    # Main properties.
    params = sa.Column(st.JsonLongDictType())

    # TODO(rakhmerov): We need to get rid of this field at all.
    context = sa.Column(st.JsonLongDictType())


class TaskExecution(Execution):
    """Contains task runtime information."""

    __mapper_args__ = {
        'polymorphic_identity': 'task_execution'
    }

    # Main properties.
    action_spec = sa.Column(st.JsonLongDictType())

    # Whether the task is fully processed (publishing and calculating commands
    # after it). It allows to simplify workflow controller implementations
    # significantly.
    processed = sa.Column(sa.BOOLEAN, default=False)

    # Data Flow properties.
    in_context = sa.Column(st.JsonLongDictType())
    published = sa.Column(st.JsonLongDictType())


for cls in utils.iter_subclasses(Execution):
    event.listen(
        # Catch and trim Execution.state_info to always fit allocated size.
        cls.state_info,
        'set',
        lambda t, v, o, i: utils.cut(v, 65532),
        retval=True
    )


def validate_long_type_length(cls, field_name, value):
    """Makes sure the value does not exceeds the maximum size."""
    if value:
        # Get the configured limit.
        size_limit_kb = cfg.CONF.engine.execution_field_size_limit_kb

        # If the size is unlimited.
        if (size_limit_kb < 0):
            return

        size_kb = int(sys.getsizeof(str(value)) / 1024)

        if size_kb > size_limit_kb:
            LOG.error(
                "Size limit %dKB exceed for class [%s], "
                "field %s of size %dKB.",
                size_limit_kb, str(cls), field_name, size_kb
            )

            raise exc.SizeLimitExceededException(
                field_name,
                size_kb,
                size_limit_kb
            )


def register_length_validator(attr_name):
    """Register an event listener on the attribute.

    This event listener will validate the size every
    time a 'set' occurs.
    """
    for cls in utils.iter_subclasses(Execution):
        if hasattr(cls, attr_name):
            event.listen(
                getattr(cls, attr_name),
                'set',
                lambda t, v, o, i: validate_long_type_length(cls, attr_name, v)
            )

# Many-to-one for 'Execution' and 'TaskExecution'.

Execution.task_execution_id = sa.Column(
    sa.String(36),
    sa.ForeignKey(TaskExecution.id),
    nullable=True
)

TaskExecution.executions = relationship(
    Execution,
    backref=backref('task_execution', remote_side=[TaskExecution.id]),
    cascade='all, delete-orphan',
    foreign_keys=Execution.task_execution_id,
    lazy='select'
)

sa.Index(
    '%s_task_execution_id' % Execution.__tablename__,
    Execution.task_execution_id
)


# Many-to-one for 'TaskExecution' and 'WorkflowExecution'.

TaskExecution.workflow_execution_id = sa.Column(
    sa.String(36),
    sa.ForeignKey(WorkflowExecution.id)
)

WorkflowExecution.task_executions = relationship(
    TaskExecution,
    backref=backref('workflow_execution', remote_side=[WorkflowExecution.id]),
    cascade='all, delete-orphan',
    foreign_keys=TaskExecution.workflow_execution_id,
    lazy='select'
)

sa.Index(
    '%s_workflow_execution_id' % TaskExecution.__tablename__,
    TaskExecution.workflow_execution_id
)


# Other objects.


class DelayedCall(mb.MistralModelBase):
    """Contains info about delayed calls."""

    __tablename__ = 'delayed_calls_v2'

    __table_args__ = (
        sa.Index(
            '%s_processing_execution_time' % __tablename__,
            'processing',
            'execution_time'
        ),
    )

    id = mb.id_column()
    factory_method_path = sa.Column(sa.String(200), nullable=True)
    target_method_name = sa.Column(sa.String(80), nullable=False)
    method_arguments = sa.Column(st.JsonDictType())
    serializers = sa.Column(st.JsonDictType())
    auth_context = sa.Column(st.JsonDictType())
    execution_time = sa.Column(sa.DateTime, nullable=False)
    processing = sa.Column(sa.Boolean, default=False, nullable=False)


class Environment(mb.MistralSecureModelBase):
    """Contains environment variables for workflow execution."""

    __tablename__ = 'environments_v2'

    __table_args__ = (
        sa.UniqueConstraint('name', 'project_id'),
        sa.Index('%s_name' % __tablename__, 'name'),
        sa.Index('%s_project_id' % __tablename__, 'project_id'),
        sa.Index('%s_scope' % __tablename__, 'scope'),
    )

    # Main properties.
    id = mb.id_column()
    name = sa.Column(sa.String(200))
    description = sa.Column(sa.Text())
    variables = sa.Column(st.JsonDictType())


def _get_hash_function_by(column_name):
    def calc_hash(context):
        d = context.current_parameters[column_name] or {}

        return hashlib.sha256(json.dumps(sorted(d.items())).
                              encode('utf-8')).hexdigest()

    return calc_hash


class CronTrigger(mb.MistralSecureModelBase):
    """Contains info about cron triggers."""

    __tablename__ = 'cron_triggers_v2'

    __table_args__ = (
        sa.UniqueConstraint('name', 'project_id'),
        sa.UniqueConstraint(
            'workflow_input_hash', 'workflow_name', 'pattern', 'project_id',
            'workflow_params_hash', 'remaining_executions',
            'first_execution_time'
        ),
        sa.Index(
            '%s_next_execution_time' % __tablename__,
            'next_execution_time'
        ),
        sa.Index('%s_project_id' % __tablename__, 'project_id'),
        sa.Index('%s_scope' % __tablename__, 'scope'),
        sa.Index('%s_workflow_name' % __tablename__, 'workflow_name'),
    )

    id = mb.id_column()
    name = sa.Column(sa.String(200))
    pattern = sa.Column(
        sa.String(100),
        nullable=True,
        default='0 0 30 2 0'  # Set default to 'never'.
    )
    first_execution_time = sa.Column(sa.DateTime, nullable=True)
    next_execution_time = sa.Column(sa.DateTime, nullable=False)
    workflow_name = sa.Column(sa.String(80))
    remaining_executions = sa.Column(sa.Integer)

    workflow_id = sa.Column(
        sa.String(36),
        sa.ForeignKey(WorkflowDefinition.id)
    )
    workflow = relationship('WorkflowDefinition', lazy='joined')

    workflow_params = sa.Column(st.JsonDictType())
    workflow_params_hash = sa.Column(
        sa.CHAR(64),
        default=_get_hash_function_by('workflow_params')
    )
    workflow_input = sa.Column(st.JsonDictType())
    workflow_input_hash = sa.Column(
        sa.CHAR(64),
        default=_get_hash_function_by('workflow_input')
    )

    trust_id = sa.Column(sa.String(80))

    def to_dict(self):
        d = super(CronTrigger, self).to_dict()

        mb.datetime_to_str(d, 'first_execution_time')
        mb.datetime_to_str(d, 'next_execution_time')

        return d


# Register all hooks related to secure models.
mb.register_secure_model_hooks()

# Register an event listener to verify that the size of all the long columns
# affected by the user do not exceed the limit configuration.
for attr_name in ['input', 'output', 'params', 'published']:
    register_length_validator(attr_name)


class ResourceMember(mb.MistralModelBase):
    """Contains info about resource members."""

    __tablename__ = 'resource_members_v2'
    __table_args__ = (
        sa.UniqueConstraint(
            'resource_id',
            'resource_type',
            'member_id'
        ),
    )

    id = mb.id_column()
    resource_id = sa.Column(sa.String(80), nullable=False)
    resource_type = sa.Column(
        sa.String(50),
        nullable=False,
        default='workflow'
    )
    project_id = sa.Column(sa.String(80), default=security.get_project_id)
    member_id = sa.Column(sa.String(80), nullable=False)
    status = sa.Column(sa.String(20), nullable=False, default="pending")
