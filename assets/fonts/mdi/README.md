# Vendored Material Design Icons (MDI) webfont

The file-type / node-type icons (`ui/components/file_type_icon.py`,
`ui/components/tree.py`) use Material Design Icons. NiceGUI/Quasar know the
`mdi-*` icon names but do **not** bundle the MDI webfont, and this app ships as
an offline PyInstaller desktop binary (no CDN), so the font must be vendored
here. `ui/theme.py:mdi_font_head_html()` injects:

    <link rel="stylesheet" href="/assets/fonts/mdi/css/materialdesignicons.min.css">

served by `register_static_assets()` (mounts `assets/` at `/assets`). The whole
`assets/` tree is already bundled by PyInstaller (`exlab_wizard.spec`:
`DATAS.append(("assets", "assets"))`), so no spec change is needed once the
files below are present.

## Required files (place alongside this README)

    assets/fonts/mdi/css/materialdesignicons.min.css
    assets/fonts/mdi/fonts/materialdesignicons-webfont.woff2
    assets/fonts/mdi/LICENSE          # Apache-2.0

The `.min.css` references the woff2 via the relative `../fonts/...` URL, which
resolves correctly under `/assets/fonts/mdi/`. Optionally trim the css
`@font-face` `src` to the `woff2` entry to avoid 404s on the unbundled
eot/ttf/woff formats.

## How to fetch (on a networked machine)

    npm pack @mdi/font@7
    tar -xzf mdi-font-7.*.tgz
    mkdir -p assets/fonts/mdi/css assets/fonts/mdi/fonts
    cp package/css/materialdesignicons.min.css        assets/fonts/mdi/css/
    cp package/fonts/materialdesignicons-webfont.woff2 assets/fonts/mdi/fonts/
    cp package/LICENSE                                 assets/fonts/mdi/LICENSE

(Or download those three files from the `@mdi/font` v7 GitHub release.)

Until the font is present, the `<link>` 404s and glyphs render as empty boxes;
the unit tests do not require the binary.
