#!/bin/bash
# Compilation continue du rapport : recompile a chaque sauvegarde du .tex.
#
# Equivalent local du bouton « Recompile » d'Overleaf, sans limite de temps.
# Une compilation complete prend ~5 s ; en mode continu, seule la premiere
# passe est refaite a chaque sauvegarde (~1,6 s), les passes suivantes n'etant
# lancees que lorsque les references bougent.
#
#   ./surveiller.sh              surveille rapport_PRe.tex (francais)
#   ./surveiller.sh --en         surveille rapport_PRe_EN.tex (anglais)
#   ./surveiller.sh --en --triple  force trois passes (references sures)
#
# Arret : Ctrl+C.

set -u
cd "$(dirname "$0")" || exit 1

SRC=rapport_PRe.tex
OUT=build
TRIPLE=""
for arg in "$@"; do
    case "$arg" in
        --en)     SRC=rapport_PRe_EN.tex ;;
        --fr)     SRC=rapport_PRe.tex ;;
        --triple) TRIPLE=--triple ;;
        *.tex)    SRC=$arg ;;
    esac
done

mkdir -p "$OUT"

compiler() {
    local passes=1
    [ "$TRIPLE" = "--triple" ] && passes=3
    local t0 t1
    t0=$(date +%s.%N)
    for ((i=1; i<=passes; i++)); do
        pdflatex -interaction=nonstopmode -output-directory="$OUT" "$SRC" \
            > "$OUT/veille.log" 2>&1
    done
    t1=$(date +%s.%N)

    local pages erreurs
    pages=$(grep -a -oE '\([0-9]+ pages' "$OUT/veille.log" | grep -oE '[0-9]+' | tail -1)
    erreurs=$(grep -ac '^!' "$OUT/veille.log")

    printf "[%s] " "$(date +%H:%M:%S)"
    if [ "$erreurs" -gt 0 ]; then
        printf "\033[31mECHEC\033[0m : %s erreur(s)\n" "$erreurs"
        grep -a -A 2 '^!' "$OUT/veille.log" | head -8 | sed 's/^/    /'
    else
        printf "\033[32mOK\033[0m : %s pages en %.1fs\n" "${pages:-?}" \
               "$(echo "$t1 - $t0" | bc)"
    fi
}

echo "Surveillance de $SRC. Ctrl+C pour arreter."
echo "Le PDF est dans $OUT/${SRC%.tex}.pdf ; ouvre-le dans une visionneuse"
echo "qui recharge automatiquement (l'apercu VS Code, ou SumatraPDF)."
echo

compiler
empreinte=$(stat -c %Y "$SRC")

while true; do
    sleep 1
    nouvelle=$(stat -c %Y "$SRC" 2>/dev/null) || continue
    if [ "$nouvelle" != "$empreinte" ]; then
        empreinte=$nouvelle
        compiler
    fi
done
