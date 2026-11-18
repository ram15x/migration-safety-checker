"""Build mutants of the full-stack-fastapi-template 0.10.0 release: copies whose created_at migration is rewritten.

Usage: make_mutants.py <0.10.0 checkout> <output directory>
"""
import re
import shutil
import sys
from pathlib import Path

REV = "backend/app/alembic/versions/fe56fa70289e_add_created_at_to_user_and_item.py"
ORIGINAL = """    op.add_column('item', sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user', sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
"""
NOT_NULL = """    op.add_column('item', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.add_column('user', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
"""
NOT_NULL_DEFAULT = """    op.add_column('item', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')))
    op.add_column('user', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')))
"""
BODIES = {
    "M1_not_null_no_default": NOT_NULL,
    "M2_not_null_server_default": NOT_NULL_DEFAULT,
    "M3_drop_user_full_name": ORIGINAL + "    op.drop_column('user', 'full_name')\n",
    "M4_rename_user_email": ORIGINAL + "    op.alter_column('user', 'email', new_column_name='email_address')\n",
    "M5_add_index": ORIGINAL + "    op.create_index('ix_item_title', 'item', ['title'])\n",
    "M6_email_varchar_10": ORIGINAL + "    op.alter_column('user', 'email', type_=sa.String(length=10), existing_nullable=False)\n",
    "M7_unique_item_title": ORIGINAL + "    op.create_unique_constraint('uq_item_title', 'item', ['title'])\n",
    "M8_check_title_not_blank": ORIGINAL + "    op.create_check_constraint('ck_item_title_not_blank', 'item', \"char_length(trim(title)) > 0\")\n",
    "M9_add_audit_table": ORIGINAL + "    op.create_table('audit_log', sa.Column('id', sa.Integer, primary_key=True), sa.Column('note', sa.Text))\n",
}


def main(src: Path, out: Path) -> None:
    for name, body in BODIES.items():
        dest = out / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
        path = dest / REV
        text = path.read_text()
        if ORIGINAL not in text:
            raise SystemExit(f"original migration body not found in {path}")
        path.write_text(text.replace(ORIGINAL, body, 1))
        print("built", name)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
