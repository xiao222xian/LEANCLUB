"""Explicit member-order schema definitions; this module never opens a database.

Existing databases must use scripts/migrate_member_orders.py. Fresh databases may
create this schema directly. No rebuild is performed by application startup.
"""
VERSION = 'leanclub-member-orders-v1'
LEGACY_COLUMNS = (
    'id', 'event_id', 'visitor', 'request_id', 'ticket_number', 'name', 'phone',
    'status', 'created', 'checked_in', 'cancelled', 'checked_by', 'check_request_id',
    'payment_status', 'amount_fen', 'payment_expires', 'paid_at',
    'payment_reference', 'paid_by', 'refund_at', 'refund_reference')
OWNER_COLUMNS = ('member_tenant_id', 'member_user_id', 'owner_bound_at')


def registration_sql(table='campaign_registrations'):
    if table not in ('campaign_registrations', 'campaign_registrations_member_v1'):
        raise ValueError('Unexpected migration table.')
    return '''CREATE TABLE IF NOT EXISTS %s (
        id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES campaign_events(id),
        visitor TEXT REFERENCES campaign_visitors(token_hash),
        request_id TEXT NOT NULL, ticket_number TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL, phone TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('registered','checked_in','cancelled')),
        created INTEGER NOT NULL, checked_in INTEGER, cancelled INTEGER,
        checked_by TEXT, check_request_id TEXT,
        payment_status TEXT NOT NULL DEFAULT 'pending_payment',
        amount_fen INTEGER NOT NULL DEFAULT 0, payment_expires INTEGER NOT NULL DEFAULT 0,
        paid_at INTEGER, payment_reference TEXT, paid_by TEXT,
        refund_at INTEGER, refund_reference TEXT,
        member_tenant_id INTEGER, member_user_id INTEGER, owner_bound_at INTEGER,
        UNIQUE(visitor,request_id),
        CHECK ((member_tenant_id IS NULL AND member_user_id IS NULL
                AND visitor IS NOT NULL AND owner_bound_at IS NULL)
            OR (typeof(member_tenant_id)='integer' AND member_tenant_id>0
                AND typeof(member_user_id)='integer' AND member_user_id>0
                AND typeof(owner_bound_at)='integer' AND owner_bound_at>0))
    )''' % table


def ownership_columns_ready(db):
    columns = {row[1]: row for row in db.execute('PRAGMA table_info(campaign_registrations)')}
    return all(name in columns for name in OWNER_COLUMNS) and 'visitor' in columns and not columns['visitor'][3]


def create_member_metadata(db, stamp):
    """Add indexes/metadata only after the ownership-capable table is present."""
    if not ownership_columns_ready(db):
        raise ValueError('Member order migration is required.')
    db.execute('''CREATE INDEX IF NOT EXISTS campaign_registration_member ON
        campaign_registrations(member_tenant_id,member_user_id,created DESC,id DESC)''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS campaign_registration_member_active ON
        campaign_registrations(member_tenant_id,member_user_id,event_id)
        WHERE member_user_id IS NOT NULL AND status!='cancelled' ''')
    db.execute('''CREATE TABLE IF NOT EXISTS campaign_member_requests (
        tenant_id INTEGER NOT NULL CHECK(tenant_id>0),
        member_user_id INTEGER NOT NULL CHECK(member_user_id>0),
        action TEXT NOT NULL CHECK(action IN ('register','cancel')),
        request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
        registration_id TEXT NOT NULL REFERENCES campaign_registrations(id),
        created INTEGER NOT NULL,
        PRIMARY KEY(tenant_id,member_user_id,action,request_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS leanclub_schema_migrations (
        version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)''')
    db.execute('INSERT OR IGNORE INTO leanclub_schema_migrations VALUES(?,?)', (VERSION, stamp))


def member_schema_ready(db):
    if not ownership_columns_ready(db):
        return False
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'campaign_member_requests', 'leanclub_schema_migrations'} <= tables:
        return False
    return db.execute('SELECT 1 FROM leanclub_schema_migrations WHERE version=?', (VERSION,)).fetchone() is not None
