# AutoOFFBEAT : image Docker
#
# Contient OpenFOAM v2506, le solveur OFFBEAT compile depuis les sources, et le
# code Python du projet. Ne contient PAS de modele de langage : Ollama tourne
# sur l'hote et l'image s'y connecte (voir plus bas).
#
#   docker build -t autooffbeat:latest .
#
# Verification sans modele de langage ni interface :
#   docker run --rm autooffbeat:latest python3 verify.py
#
# Simulation complete, resultats ecrits sur l'hote :
#   docker run --rm -v "$PWD/out:/autooffbeat/out" autooffbeat:latest \
#     bash -lc 'source $OPENFOAM_BASHRC && python3 run_sim.py'
#
# Interface web, avec Ollama sur l'hote :
#   docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway \
#     -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
#     autooffbeat:latest
#
# NOTE : cette image n'a pas ete construite ni testee sur la machine de
# developpement, Docker n'y etant pas installe. Les versions, l'adresse du
# depot et la commande de compilation correspondent a l'installation locale
# qui produit les resultats du rapport, mais le build lui-meme reste a valider.

# ── Etape 1 : base OpenFOAM ──────────────────────────────────────────────────
# v2506 : meme version que celle utilisee pour tous les resultats du rapport.
FROM opencfd/openfoam-default:2506 AS of-base
ENV OPENFOAM_BASHRC=/usr/lib/openfoam/openfoam2506/etc/bashrc

# ── Etape 2 : compilation d'OFFBEAT ──────────────────────────────────────────
FROM of-base AS offbeat-build
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Depot public reel. L'adresse donnee par la documentation interne du projet
# etait perimee : voir la section 5.1.2 du rapport.
ARG OFFBEAT_REPO=https://gitlab.com/foam-for-nuclear/offbeat.git
ARG OFFBEAT_BRANCH=master

# La compilation passe par le makefile GNU fourni, qui enchaine les cibles
# wmake (SCIANTIX, offbeatLib, offbeat, utilities, functionObjects).
# Il n'y a pas de script Allwmake dans ce depot.
RUN git clone --depth 1 --branch ${OFFBEAT_BRANCH} ${OFFBEAT_REPO} /opt/offbeat \
    && bash -lc "source ${OPENFOAM_BASHRC} \
        && cd /opt/offbeat \
        && make -j\$(nproc) 2>&1 | tee /opt/offbeat/build.log"

# ── Etape 3 : image finale ───────────────────────────────────────────────────
FROM of-base AS final
USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /autooffbeat

# wmake depose les binaires dans FOAM_USER_APPBIN, sous le home du compilateur.
COPY --from=offbeat-build /root/OpenFOAM /root/OpenFOAM
RUN set -eux; \
    bin="$(find /root/OpenFOAM -type f -name offbeat -perm -u+x | head -1)"; \
    test -n "$bin"; \
    ln -sf "$bin" /usr/local/bin/offbeat

# Environnement isole : l'image est mono-application, pas besoin de venv, mais
# les distributions recentes marquent le site-packages systeme comme gere par
# l'OS (PEP 668).
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY . .
RUN mkdir -p AutoOFFBEAT_logs out

ENV OFFBEAT_BIN=/usr/local/bin/offbeat
ENV BLOCKMESH_BIN=/usr/lib/openfoam/openfoam2506/platforms/linux64GccDPInt32Opt/bin/blockMesh
# Par defaut on vise un Ollama sur l'hote ; surchargez avec -e si besoin.
ENV OLLAMA_BASE_URL=http://host.docker.internal:11434
ENV OLLAMA_NUM_CTX=16384
ENV MPLCONFIGDIR=/tmp/matplotlib
ENV PORT=8000

EXPOSE 8000

# Verification rapide, sans modele de langage :
#   docker run --rm autooffbeat:latest python3 verify.py
CMD ["bash", "-lc", "source $OPENFOAM_BASHRC && python3 app.py"]
