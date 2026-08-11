# Exemple : barreau PWR à 25 kW/m sur ~1 an

Paire « demande utilisateur → cas généré », pour guider l'agent (few-shot).

## Demande utilisateur

> « Simule un barreau combustible REP avec une puissance linéique de
> 25 kW/m, un rayon de pastille de 4.6 mm et un enrichissement de 5 %,
> sur environ deux ans. »

## Raisonnement attendu de l'agent

1. C'est un barreau 1D PWR standard → template `fuel_rod_1D_pwr`.
2. Mapper les grandeurs vers les paramètres reconnus :
   - 25 kW/m → `linear_heat_rate: 25000` (W/m)
   - rayon pastille 4.6 mm → `fuel_outer_radius: [4.6]` (mm, convention rodDict)
   - enrichissement 5 % → `enrichment: 0.05`
   - ~2 ans → `end_time: 6.3e7` (s)

## Appel input_creator

```json
{
  "case_dir": "/host/pwr_rod_25kw",
  "template_name": "fuel_rod_1D_pwr",
  "params": "{\"linear_heat_rate\": 25000, \"fuel_outer_radius\": [4.6], \"enrichment\": 0.05, \"end_time\": \"6.3e7\"}"
}
```

## Puis offbeat_executor

```json
{ "case_dir": "/host/pwr_rod_25kw", "self_healing": true }
```

→ génère le maillage (rodMaker + blockMesh), lance `offbeat`, et auto-répare
en cas de dépassement du nombre de Courant ou de non-convergence.

## Puis data_processor

```json
{ "case_dir": "/host/pwr_rod_25kw", "analysis": "summary" }
```

→ profils axial/radial de température, contrainte de cerclage, PCT.

## Résultat attendu

Température au centre de la pastille nettement supérieure à la périphérie
(gradient radial typique de quelques centaines de K à 25 kW/m), montée de
pression de gap au cours de l'irradiation via le couplage FGR (SCIANTIX).
