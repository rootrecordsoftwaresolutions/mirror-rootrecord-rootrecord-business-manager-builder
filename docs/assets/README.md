# Product documentation images

These files support **[`../PRODUCT_README.md`](../PRODUCT_README.md)**. Paths in that document are relative to the `docs/` folder (for example `assets/banner.jpg`).

## Files in use

| File | Role |
|------|------|
| `banner.jpg` | Wide banner at the top of the product readme |
| `dashboard_image.jpg` | Dashboard feature screenshot |
| `github-icon.jpg` | Footer / branding image (sourced from your main GitHub icon artwork) |

Source artwork can live under **RR Business Operations** (workspace root): `banner.jpg`, `dashboard_image.jpg`, and `main gitgub icon.jpg` — run **`build\sync_public_github_docs.ps1`** to refresh `docs\assets` copies before editing the readme, or copy into this folder manually.

## Checklist before a release

- [ ] Image filenames match the table above so links in `PRODUCT_README.md` stay valid.
- [ ] Every `![...](...)` in `PRODUCT_README.md` has meaningful **alt** text.
