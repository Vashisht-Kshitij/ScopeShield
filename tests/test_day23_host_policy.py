import json
from pathlib import Path
import pytest
from deploy.azure.host import manage


def test_same_schema_without_safe_backup_and_quote_policy_is_not_rollback_compatible(tmp_path):
    with pytest.raises(ValueError,match='current evidence and backup policy'):
        manage.require_release_policy(tmp_path)


@pytest.mark.parametrize('change',[{}, {'encryptedBackupMaximumBytes':128*1024*1024}, {'contractQuotes':'first-match'}])
def test_reviewed_release_requires_exact_current_policy(tmp_path,change):
    policy=json.loads(Path('deploy/azure/host/release-policy.json').read_text(encoding='utf-8'))
    policy.update(change)
    path=tmp_path/'deploy/azure/host/release-policy.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(policy))
    if change:
        with pytest.raises(ValueError):manage.require_release_policy(tmp_path)
    else:manage.require_release_policy(tmp_path)
