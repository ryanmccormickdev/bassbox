# Fonts

`static/` holds Latin-subset static instances of five SIL Open Font License 1.1 families, built
from their variable fonts by `../tools/make_fonts.py` (static instances matter: HarfBuzz otherwise
shapes with default-instance advances while FreeType renders the requested weight, which breaks
spacing). The licence text of each family is next to this file.

| Family | Version | Source |
|---|---|---|
| Inter | 4.001 | https://github.com/rsms/inter |
| Space Grotesk | 2.000 | https://github.com/floriankarsten/space-grotesk |
| JetBrains Mono | 2.211 | https://github.com/JetBrains/JetBrainsMono |
| Archivo | 2.001 | https://github.com/Omnibus-Type/Archivo |
| Unbounded | 1.701 | https://github.com/googlefonts/unbounded |

The variable sources are not included. To rebuild the instances, put each family's variable
`.ttf` in `src/` (`Inter.ttf`, `JetBrainsMono.ttf`, `SpaceGrotesk.ttf`, `Archivo.ttf`,
`Unbounded.ttf`) and run `python3 ui/tools/make_fonts.py` (needs `fonttools`).
