# Rapport PRe — AutoOFFBEAT

Premier jet du rapport de stage, structuré selon `CONTENU DU RAPPORT_PRe.pdf`
et calqué sur `Rapport_Modele_PRe.pdf`.

## Deux versions

| Fichier | Langue | Pages |
|---|---|---|
| `rapport_PRe.tex` | français | 52 |
| `rapport_PRe_EN.tex` | **anglais — version à rendre** | 52 |

La version anglaise est assemblée depuis `en/p01…p16.tex` (un fichier par
partie). **Toute correction doit être faite dans `en/`**, puis réassemblée :

```bash
cat en/p*.tex > rapport_PRe_EN.tex
```

Les deux versions partagent la même structure : mêmes numéros de figures, de
tableaux et de sections, mêmes valeurs numériques (vérifié : les 114
coordonnées pgfplots et l'ensemble des littéraux `\SI`/`\num` sont
identiques d'une version à l'autre). Seuls la prose, les légendes et les
étiquettes d'axes sont traduits. Le résumé français est conservé dans la
version anglaise (`\begin{otherlanguage}{french}`), les consignes PRe
exigeant l'abstract dans les deux langues.

## Compilation

```bash
./compiler.sh          # version française
./compiler.sh --en     # version anglaise  (~5 s, 3 passes)
./compiler.sh --en --ouvrir

./surveiller.sh --en   # recompile à chaque sauvegarde (~1,6 s)
```

Ou à la main :

```bash
pdflatex rapport_PRe_EN.tex
pdflatex rapport_PRe_EN.tex   # 2e passe : table des matières
pdflatex rapport_PRe_EN.tex   # 3e passe : références de pages des annexes
```

Paquets requis (Debian/Ubuntu) :

```bash
sudo apt install -y texlive-latex-recommended texlive-latex-extra \
                    texlive-pictures texlive-science texlive-lang-french
```

Sur Overleaf, tout est déjà disponible : déposer le `.tex` tel quel.

> Les deux versions compilent sans erreur ni référence non résolue.
> Débordements de marge : 9 (fr), 5 (en), tous inférieurs à 4,2 pt.

## À compléter avant remise

### 1. Bloc d'identité (en tête du `.tex`, ligne ~60)

Tout est centralisé dans des `\newcommand` : les modifier une fois suffit,
la page de titre et les pieds de page se mettent à jour partout.

| Commande | À renseigner |
|---|---|
| `\maSpecialite` | spécialité ENSTA |
| `\maPromotion` | promotion |
| `\organisme`, `\adresseOrganisme` | organisme d'accueil |
| `\tuteurENSTA`, `\tuteurOrganisme` | noms des tuteurs |
| `\dateDebut`, `\dateFin` | dates du stage |
| `\mentionConfidentialite` | mention exacte (affichée en rouge) |
| `\titreRapport`, `\sousTitreRapport` | titre proposé — **à retravailler** |

Vérifier aussi `\monNom` / `\monPrenom` (pré-remplis « BUTEL / Yann »).

### 2. Logos — fait

`Logo_ENSTA.png` et `Logo_SPEIT.png` sont dans ce dossier et chargés via
`\graphicspath{{Figures/}{./}}`.

### 3. Passages marqués en rouge

Chercher `\color{red}` dans le source — chaque occurrence signale un passage
à personnaliser ou à vérifier :

- note de (non) confidentialité : choisir parmi les trois variantes ;
- remerciements : à personnaliser (ordre hiérarchique + fonctions) ;
- planning du stage : ajuster les semaines aux dates réelles ;
- bibliographie : compléter la référence AutoFLUKA, vérifier les références
  OFFBEAT sur les versions publiées, dater les consultations d'URL.

### 4. Vérifications de fond

- **Les seuils de `safety_kb.json` sont présentés comme non validés** dans tout
  le rapport (§ Annexe D notamment). Si l'encadrant les valide d'ici la remise,
  mettre à jour le texte en conséquence.
- **Dates et durée du stage** : le planning est en 16 semaines, à ajuster.
- Faire relire l'orthographe (les consignes y insistent).

## Ce que contient le rapport

| Partie | Contenu |
|---|---|
| Ch. 1 | Physique du crayon, code OFFBEAT, principe des agents LLM, périmètre |
| Ch. 2 | Architecture de l'agent : superviseur, outils, auto-réparation, RAG |
| Ch. 3 | Jumeau numérique D1→D5 (sûreté, pronostic, assimilation, tableau de bord, émulateur) |
| Ch. 4 | Résultats : simulation 2 ans, validation de l'émulateur, mise à l'épreuve du self-healing, défauts de justesse corrigés |
| Ch. 5 | Difficultés, fausses routes, planning |
| Annexes | Environnement, structure d'un cas, bases de connaissance, jeu de données |

**Toutes les valeurs numériques proviennent de runs réels** (cas
`pwr_rod_25kw_2y_v2`, dataset de l'émulateur, mesures de latence). Aucune
donnée n'est illustrative.

### Figures générées en natif (TikZ / pgfplots)

Aucune image externe n'est nécessaire, tout est vectoriel :

1. organigramme de l'agent ;
2. boucle d'auto-réparation ;
3. évolution des températures pastille / gaine ;
4. fermeture du gap + contrainte de cerclage ;
5. profil radial de température ;
6. diagramme de parité de l'émulateur.

## Points d'attention pour la soutenance

Les consignes valorisent explicitement l'exposé des erreurs et fausses routes
« accompagné d'une réflexion montrant vos progrès ». Le rapport s'appuie
là-dessus dans deux sections qui sont ses plus originales :

- **§4.3** — les trois plantages provoqués délibérément, dont **deux échecs**
  du self-healing, avec l'analyse de *pourquoi* il échoue (limite de nature :
  motif ≠ cause racine) ;
- **§5.2** — l'approximation « conservative » qui ne l'était pas, et le
  recul sur l'usage d'un assistant de programmation.

C'est probablement l'angle le plus défendable à l'oral : ne pas présenter un
outil qui marche, mais un outil dont on a mesuré les limites.
