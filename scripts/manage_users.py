"""Local operator CLI. Passwords enter through getpass, never arguments or output.

python -m scripts.manage_users create --username owner.local --role freelancer
python -m scripts.manage_users create --username client.local --role client
python -m scripts.manage_users assign-legacy --username owner.local --project-id UUID
python -m scripts.manage_users reset-password --username owner.local
"""
import argparse
from getpass import getpass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import hash_password, normalize_username
from app.database import build_engine, database_url, initialize_database
from app.storage import AuthSession, Project, SecurityEvent, User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "reset-password", "assign-legacy", "list-legacy"])
    parser.add_argument("--username")
    parser.add_argument("--role", choices=["freelancer", "client"])
    parser.add_argument("--project-id", type=UUID)
    args = parser.parse_args()
    if args.action != "list-legacy" and not args.username:
        parser.error("--username is required for account operations")
    username = normalize_username(args.username) if args.username else None
    engine = build_engine(database_url())
    initialize_database(engine)
    if args.action == "list-legacy":
        with Session(engine) as session:
            for project_id in session.scalars(select(Project.id).where(Project.owner_id.is_(None)).order_by(Project.created_at)):
                print(project_id)
        engine.dispose()
        return
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.username == username))
        if args.action == "assign-legacy":
            if not user or user.role != "freelancer" or not args.project_id:
                parser.error("An existing freelancer and explicit --project-id are required")
            project = session.get(Project, str(args.project_id))
            if not project or project.owner_id is not None:
                parser.error("Only an existing unassigned legacy project can be assigned")
            project.owner_id = user.id
            session.add(SecurityEvent(actor_id=user.id, action="LEGACY_PROJECT_ASSIGNED", resource_id=project.id))
        else:
            if args.action == "create" and (user or not args.role):
                parser.error("A new username and explicit --role are required")
            if args.action == "reset-password" and not user:
                parser.error("Account not found")
            password = getpass("Password (12–128 characters, hidden): ")
            if password != getpass("Confirm password (hidden): "):
                parser.error("Passwords do not match")
            encoded = hash_password(password)
            del password
            if user:
                user.password_hash = encoded
                session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            else:
                user = User(username=username, display_name=username, role=args.role, password_hash=encoded)
                session.add(user)
                session.flush()
            session.add(SecurityEvent(actor_id=user.id, action="ACCOUNT_" + ("CREATED" if args.action == "create" else "PASSWORD_RESET")))
        session.commit()
    engine.dispose()
    print("Local account operation completed. No password was printed or saved in plaintext.")


if __name__ == "__main__":
    main()
