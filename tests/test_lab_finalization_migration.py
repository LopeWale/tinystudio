"""Additive operation kind migration preserves existing rows and retained history."""
import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from tests.test_lab_transfer_service import database
from tinystudio.db.migrate import downgrade, head_revision, upgrade
from tinystudio.db.models import Project
from tinystudio.lab.contracts import checksum
from tinystudio.lab.models import LabOperation


def test_finalization_kind_upgrade_and_guarded_downgrade_are_additive(database):
    engine=database.kw['bind'];url=engine.url.render_as_string(hide_password=False)
    downgrade('j3f5a9078c61',url)
    with database.begin() as db:
        project=Project(slug=uuid.uuid4().hex,name='Migration sentinel');db.add(project);db.flush();project_id=project.id
        old=LabOperation(project_id=project_id,kind='export',idempotency_key='existing',payload={},payload_digest=checksum({}))
        db.add(old);db.flush();old_id=old.id
    upgrade('head',url)
    with database.begin() as db:
        assert db.get(Project,project_id).name=='Migration sentinel'
        assert db.get(LabOperation,old_id).kind=='export'
        row=LabOperation(project_id=project_id,kind='attempt_finalize',idempotency_key='new',payload={},payload_digest=checksum({}))
        db.add(row);db.flush();identifier=row.id
    with database() as db:
        row=db.get(LabOperation,identifier);row.payload={'rewritten':True}
        with pytest.raises(DBAPIError,match='identity is immutable'):db.commit()
        db.rollback()
    with pytest.raises(DBAPIError,match='retain attempt finalization history'):
        downgrade('j3f5a9078c61',url)
    with engine.connect() as connection:
        from sqlalchemy import text
        assert connection.execute(text('select version_num from alembic_version')).scalar()==head_revision()
    with database() as db:
        assert db.get(LabOperation,identifier).kind=='attempt_finalize'
        assert db.get(LabOperation,identifier).payload=={}
        assert db.get(LabOperation,old_id).kind=='export'
