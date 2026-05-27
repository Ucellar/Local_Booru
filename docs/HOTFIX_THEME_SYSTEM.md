# Theme system hotfix

Fixed theme switching problems:

- `r34` now loads the real R34 purple theme, not Sakura.
- `pornhub` now loads the PornHub theme instead of falling back to Abyss.
- Old lowercase `#sidebar` selectors were updated to the current `#Sidebar` object name.
- Old `[active=true]` nav selector was replaced with `QPushButton#NavBtn:checked`.
- Removed hardcoded styles from logo, workspace button, workspace menu and stacked widget that blocked global themes.
- Added `dark` palette alias.

Result: theme choice in Settings should consistently affect sidebar, logo, workspace button/menu, tables and common widgets.
