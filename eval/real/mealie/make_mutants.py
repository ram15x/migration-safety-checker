"""Build mutants of the Mealie v3.25.0 release: copies whose tokens_valid_after migration (69e942bab3aa) is rewritten.

Usage: make_mutants.py <v3.25.0 checkout> <output directory>
"""
import shutil
import sys
from pathlib import Path

ORIGINAL = '        batch_op.add_column(sa.Column("tokens_valid_after", mealie.db.migration_types.NaiveDateTime(), nullable=True))\n'
BODIES = {
    "MM1_drop_users_full_name": ORIGINAL + '        batch_op.drop_column("full_name")\n',
    "MM2_rename_recipes_name": ORIGINAL + '    op.alter_column("recipes", "name", new_column_name="recipe_name")\n',
    "MM3_not_null_no_default": ORIGINAL.replace("nullable=True", "nullable=False"),
    "MM4_not_null_server_default": ORIGINAL.replace(
        "nullable=True", 'nullable=False, server_default=sa.text("now()")'),
}


def main(src: Path, out: Path) -> None:
    (path,) = list((src / "mealie/alembic/versions").glob("*69e942bab3aa*.py"))
    for name, body in BODIES.items():
        dest = out / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "frontend", "node_modules", ".venv", "tests"))
        target = dest / path.relative_to(src)
        text = target.read_text()
        if ORIGINAL not in text:
            raise SystemExit(f"original migration body not found in {target}")
        if name.startswith("MM2"):
            # the rename is an op-level call, so it goes after the batch block
            text = text.replace(ORIGINAL, ORIGINAL + "\n" + body.split(ORIGINAL)[1], 1)
        else:
            text = text.replace(ORIGINAL, body, 1)
        target.write_text(text)
        print("built", name)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
