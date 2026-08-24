#!/bin/bash
# Compilation locale du rapport PRe.
#
# Overleaf en version gratuite impose un delai de compilation court. Compiler
# ici prend environ 5 secondes (3 passes) et n'a aucune limite.
#
#   ./compiler.sh              compile le rapport
#   ./compiler.sh --ouvrir     compile puis ouvre le PDF
#   ./compiler.sh mon.tex      compile un fichier explicite
#
# Prerequis (deja installes) :
#   texlive-latex-recommended texlive-latex-extra texlive-pictures
#   texlive-science texlive-lang-french texlive-fonts-recommended

set -u
cd "$(dirname "$0")" || exit 1

SRC=rapport_PRe_EN.tex
OUT=build
OUVRIR=""
for arg in "$@"; do
    case "$arg" in
        --en)     SRC=rapport_PRe_EN.tex ;;   # conserve : ancienne habitude
        --ouvrir) OUVRIR=1 ;;
        *.tex)    SRC=$arg ;;
    esac
done

mkdir -p "$OUT"

echo "Compilation de $SRC (3 passes)..."
debut=$(date +%s.%N)
for passe in 1 2 3; do
    pdflatex -interaction=nonstopmode -output-directory="$OUT" "$SRC" \
        > "$OUT/passe$passe.log" 2>&1
    code=$?
    if [ $code -ne 0 ] && [ ! -f "$OUT/${SRC%.tex}.pdf" ]; then
        echo "ECHEC a la passe $passe. Premieres erreurs :"
        grep -a -A 3 '^!' "$OUT/passe$passe.log" | head -20
        exit 1
    fi
done
fin=$(date +%s.%N)

PDF="$OUT/${SRC%.tex}.pdf"
pages=$(grep -a -oE 'Output written on .* \(([0-9]+) pages' "$OUT/passe3.log" \
        | grep -oE '[0-9]+ pages' | head -1)
erreurs=$(grep -ac '^!' "$OUT/passe3.log")
nonres=$(grep -ac 'undefined' "$OUT/passe3.log")
debord=$(grep -ac 'Overfull' "$OUT/${SRC%.tex}.log")

echo
echo "--------------------------------------------------"
printf "  PDF        : %s (%s)\n" "$PDF" "${pages:-?}"
printf "  Duree      : %.1f s\n" "$(echo "$fin - $debut" | bc)"
printf "  Erreurs    : %s\n" "$erreurs"
printf "  Refs non resolues : %s\n" "$nonres"
printf "  Debordements      : %s\n" "$debord"
echo "--------------------------------------------------"

# Rappel des passages restant a personnaliser.
rouges=$(grep -c 'color{red}' "$SRC")
[ "$rouges" -gt 0 ] && echo "  $rouges passage(s) en rouge a completer (grep 'color{red}')"

if [ -n "$OUVRIR" ]; then
    if command -v explorer.exe > /dev/null; then
        explorer.exe "$(wslpath -w "$PDF")" 2>/dev/null   # WSL -> visionneuse Windows
    elif command -v xdg-open > /dev/null; then
        xdg-open "$PDF"
    fi
fi
