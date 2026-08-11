# Template : fuel_rod_1D_pwr

Cas OFFBEAT de référence — barreau combustible **1D (1.5D)** type **PWR/REP**,
UO2 enrichi à 4.5 %, gaine Zircaloy, historique de puissance et profil axial
réalistes. Issu d'un cas validé (`test_auto_pwr_1D`).

## Workflow d'exécution (cf. Allrun)

```bash
python3 rodMaker.py          # lit rodDict  -> écrit blockMeshDict
mv blockMeshDict system/
blockMesh > log.blockMesh    # construit constant/polyMesh/
offbeat | tee log.offbeat    # lance le solveur
```

Le maillage **n'est pas** livré dans le template : il est régénéré par
`rodMaker.py` + `blockMesh` à chaque exécution. C'est `offbeat_executor` qui
enchaîne ces étapes.

## Structure

| Fichier | Rôle |
|---------|------|
| `rodDict` | **Géométrie du barreau** (syntaxe Python, lue par rodMaker.py) : rayons, hauteurs, nombre de blocs/cellules. |
| `rodMaker.py` | Génère `blockMeshDict` à partir de `rodDict`. |
| `constant/solverDict` | **Dictionnaire principal OFFBEAT** : choix des solveurs (thermique, mécanique, neutronique), modèles matériaux, **historique de puissance (lhgr)**, options FGR (SCIANTIX), gap gas. |
| `constant/axialProfile` | Profil de puissance axial dépendant du temps. |
| `constant/systemPressure` | Pression du caloporteur vs temps. |
| `system/controlDict` | Contrôle temporel : `endTime`, `deltaT`, pas de temps adaptatif. |
| `system/fvSchemes`, `fvSolution` | Schémas de discrétisation et solveurs linéaires. |
| `system/probes`, `system/sample` | Sondes runtime et échantillonnage. |
| `0/{T,D,gapGas,neutronFlux0}` | Champs et conditions aux limites initiaux. |

## Paramètres surchargeables par input_creator

Clés reconnues (mode `template`, JSON `params`) :

| Clé JSON | Cible | Fichier |
|----------|-------|---------|
| `linear_heat_rate` | valeurs `lhgr (...)` | constant/solverDict |
| `end_time` | `endTime` | system/controlDict |
| `delta_t` | `deltaT` | system/controlDict |
| `write_interval` | `writeInterval` | system/controlDict |
| `fuel_outer_radius` | `'rOuterFuel'` | rodDict |
| `fuel_height` | `'heightFuel'` | rodDict |
| `clad_inner_radius` | `'rInnerClad'` | rodDict |
| `clad_outer_radius` | `'rOuterClad'` | rodDict |
| `n_cells_r_fuel` | `'nCellsRFuel'` | rodDict |
| `enrichment` | `enrichment` | constant/solverDict |

> Les clés `rodDict` sont en **syntaxe Python** (`'heightFuel': [3000],`),
> celles des dictionnaires OpenFOAM en syntaxe FOAM (`endTime  3.15E+07;`).
> `input_creator` gère les deux cas.

## Paramètres physiques par défaut

- Géométrie : pastille UO2 rayon 4.5 mm, gaine 4.565–5.315 mm, hauteur 3 m.
- Maillage 1D : 30 cellules radiales (fuel) + 10 (clad), wedge 0.25°.
- Puissance : 15 kW/m (plateau), profil axial 12 points dépendant du temps.
- Durée : `endTime = 3.15e7 s` (~1 an), pas de temps adaptatif.
- Modèles : densification/swelling/relocation UO2FRAPCON, FGR SCIANTIX,
  gap gas FRAPCON, fluage Limback (gaine) / Matpro (fuel).
