"""Administrator provisioning. Passwords are read from the terminal, never argv."""
import argparse
from getpass import getpass
import os
import sys

from campaign import CampaignStore
from store import Problem


def main():
    parser = argparse.ArgumentParser(description='LEAN SCHOOL administrator management')
    parser.add_argument('action', choices=('create-admin', 'reset-password'))
    parser.add_argument('--username', required=True)
    args = parser.parse_args()
    try:
        password = getpass('Password (12–128 characters): ')
        confirmation = getpass('Confirm password: ')
        if password != confirmation:
            print('Passwords do not match.', file=sys.stderr)
            return 1
        store = CampaignStore(os.environ.get('CLUB_DB', '/var/lib/leanmuscleclub/clubs.sqlite3'), os.environ.get('CLUB_MEDIA_DIR'))
        if args.action == 'create-admin':
            store.create_admin(args.username, password)
        else:
            store.reset_password(args.username, password)
        print('Administrator created.' if args.action == 'create-admin' else 'Password changed; existing sessions revoked.')
        return 0
    except Problem as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nCancelled.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
