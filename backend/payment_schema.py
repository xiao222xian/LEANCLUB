"""Additive payment coordination schema. Existing databases migrate offline only."""
VERSION = 'leanclub-payments-v1'
TABLES = ('campaign_payment_bindings', 'campaign_payment_outbox', 'campaign_payment_inbox',
          'campaign_payment_transactions', 'campaign_payment_refunds', 'campaign_payment_anomalies')


def payment_schema_ready(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return (set(TABLES) <= tables and 'leanclub_schema_migrations' in tables
            and db.execute('SELECT 1 FROM leanclub_schema_migrations WHERE version=?', (VERSION,)).fetchone() is not None)


def create_payment_schema(db, stamp):
    # execute each statement, not executescript: caller's transaction must survive.
    statements = (
        '''CREATE TABLE campaign_payment_bindings (
            order_id TEXT PRIMARY KEY REFERENCES campaign_registrations(id),
            merchant_order_id TEXT NOT NULL UNIQUE, attempt_id TEXT NOT NULL UNIQUE,
            out_trade_no TEXT NOT NULL, provider TEXT NOT NULL, merchant_id TEXT NOT NULL,
            app_id TEXT NOT NULL, currency TEXT NOT NULL CHECK(currency='CNY'),
            amount_fen INTEGER NOT NULL CHECK(amount_fen>0),
            create_state TEXT NOT NULL CHECK(create_state IN ('unstarted','started','observed','fenced')),
            close_reason TEXT CHECK(close_reason IN ('timeout','user_cancel','event_cancel','unfulfillable')),
            version INTEGER NOT NULL DEFAULT 1, created INTEGER NOT NULL,
            UNIQUE(provider,merchant_id,out_trade_no))''',
        '''CREATE TABLE campaign_payment_outbox (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES campaign_registrations(id),
            kind TEXT NOT NULL CHECK(kind IN ('payment','refund')),
            state TEXT NOT NULL CHECK(state IN ('pending','done')),
            attempts INTEGER NOT NULL DEFAULT 0, available_at INTEGER NOT NULL,
            lease_token TEXT, lease_until INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, created INTEGER NOT NULL, UNIQUE(order_id,kind))''',
        '''CREATE INDEX campaign_payment_work ON campaign_payment_outbox(state,available_at,lease_until)''',
        '''CREATE TABLE campaign_payment_inbox (
            provider TEXT NOT NULL, merchant_id TEXT NOT NULL, event_key TEXT NOT NULL,
            order_id TEXT NOT NULL REFERENCES campaign_registrations(id), kind TEXT NOT NULL,
            body_hash TEXT NOT NULL, result TEXT NOT NULL, observed_at INTEGER NOT NULL,
            created INTEGER NOT NULL, PRIMARY KEY(provider,merchant_id,event_key))''',
        '''CREATE TABLE campaign_payment_transactions (
            provider TEXT NOT NULL, merchant_id TEXT NOT NULL, transaction_id TEXT NOT NULL,
            order_id TEXT NOT NULL UNIQUE REFERENCES campaign_registrations(id),
            paid_at INTEGER NOT NULL,
            PRIMARY KEY(provider,merchant_id,transaction_id))''',
        '''CREATE TABLE campaign_payment_refunds (
            order_id TEXT PRIMARY KEY REFERENCES campaign_registrations(id),
            refund_id TEXT NOT NULL UNIQUE, transaction_id TEXT NOT NULL,
            provider TEXT NOT NULL, merchant_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('unstarted','started','pending','succeeded')),
            provider_refund_id TEXT, created INTEGER NOT NULL, succeeded_at INTEGER,
            UNIQUE(provider,merchant_id,provider_refund_id))''',
        '''CREATE TABLE campaign_payment_anomalies (
            order_id TEXT NOT NULL REFERENCES campaign_registrations(id),
            code TEXT NOT NULL, created INTEGER NOT NULL, PRIMARY KEY(order_id,code))''',
    )
    for statement in statements:
        db.execute(statement)
    db.execute('INSERT INTO leanclub_schema_migrations VALUES(?,?)', (VERSION, stamp))
