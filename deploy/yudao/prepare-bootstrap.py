"""Generate private first-admin SQL for a NEW EMPTY database; never connects to it."""
import argparse
from datetime import date
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import bcrypt

def sql_string(value):
    # Hex literals avoid relying on SQL quote/backslash modes.
    return "CONVERT(X'" + value.encode('utf-8').hex() + "' USING utf8mb4)"

def render(username, password_hash, expiry, oauth_secret):
    s = sql_string
    return f'''-- Private initial bootstrap. Run only after empty-database/schema review.
-- mysql batch must stop on error; NEVER use --force. No existing rows are updated.
SET NAMES utf8mb4;
CREATE TEMPORARY TABLE leanschool_bootstrap_guard (ok TINYINT CHECK (ok=1));
INSERT INTO leanschool_bootstrap_guard SELECT IF(
  (SELECT COUNT(*) FROM system_users)=0 AND
  (SELECT COUNT(*) FROM system_tenant)=0 AND
  (SELECT COUNT(*) FROM system_role)=0 AND
  (SELECT COUNT(*) FROM system_oauth2_client)=0, 1, 0);
START TRANSACTION;
INSERT INTO system_tenant (id,name,contact_user_id,contact_name,status,websites,package_id,expire_time,account_count)
VALUES (1,{s('薄肌学院')},1,{s('系统管理员')},0,{s('["www.leanschool.net"]')},0,{s(expiry + ' 23:59:59')},100);
INSERT INTO system_dept (id,name,parent_id,sort,leader_user_id,status,tenant_id)
VALUES (1,{s('薄肌学院')},0,0,1,0,1);
INSERT INTO system_users (id,username,password,nickname,dept_id,status,tenant_id)
VALUES (1,{s(username)},{s(password_hash)},{s('系统管理员')},1,0,1);
INSERT INTO system_role (id,name,code,sort,data_scope,data_scope_dept_ids,status,type,tenant_id)
VALUES (1,{s('超级管理员')},'super_admin',0,1,'[]',0,1,1);
INSERT INTO system_user_role (user_id,role_id,tenant_id) VALUES (1,1,1);
INSERT INTO system_oauth2_client
  (id,client_id,secret,name,logo,status,access_token_validity_seconds,refresh_token_validity_seconds,redirect_uris,authorized_grant_types,scopes,auto_approve_scopes)
VALUES (1,'default',{s(oauth_secret)},{s('薄肌学院管理后台')},'',0,1800,86400,'[]','["password","refresh_token"]','[]','[]');
COMMIT;
DROP TEMPORARY TABLE leanschool_bootstrap_guard;
'''

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--username', required=True)
    p.add_argument('--tenant-expiry', required=True, help='Explicit YYYY-MM-DD expiry chosen for this operator tenant')
    p.add_argument('--password-file', type=Path, help='Optional existing private UTF-8 file; value is never printed')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,29}', a.username):
        raise SystemExit('Username must be 4–30 ASCII letters/digits/underscores, starting with a letter')
    if date.fromisoformat(a.tenant_expiry) <= date.today():
        raise SystemExit('Tenant expiry must be in the future')
    password = a.password_file.read_text(encoding='utf-8').rstrip('\r\n') if a.password_file else getpass.getpass('New production administrator password: ')
    if len(password) < 16 or len(password.encode()) > 72 or len(set(password)) < 8:
        raise SystemExit('Use a unique password of at least 16 characters and at most 72 UTF-8 bytes')
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    output = render(a.username, hashed, a.tenant_expiry, secrets.token_urlsafe(48))
    fd = os.open(a.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(output)
    print(json.dumps({'created': str(a.output), 'contains': 'hash and generated OAuth client secret; keep private',
                      'databaseChanged': False}, ensure_ascii=False))

if __name__ == '__main__':
    main()
