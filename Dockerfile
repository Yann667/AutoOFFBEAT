# AutoOFFBEAT – image Docker
#
# Base : OpenFOAM 2406 (openfoam.com) + OFFBEAT compilé depuis les sources
# Équivalent du conteneur AutoFLUKA, où le mount FLUKA est remplacé par
# l'installation OpenFOAM + la compilation du solveur OFFBEAT.
#
# Build :
#   docker build -t autooffbeat:latest .
#
# Run (voir README) :
#   docker run -p 8000:8000 --env-file .env \
#     -v "$PWD/offbeat_skills:/autooffbeat/offbeat_skills" \
#     -v "$PWD/AutoOFFBEAT_logs:/autooffbeat/AutoOFFBEAT_logs" \
#     -v "/path/to/simulations:/host" \
#     autooffbeat:latest

# ── Étape 1 : image OpenFOAM officielle (openfoam.com) ───────────────────────
FROM opencfd/openfoam-default:2406 AS of-base

# ── Étape 2 : compilation d'OFFBEAT ──────────────────────────────────────────
# OFFBEAT est un solveur open-source (gitlab.com/offbeat-solver/offbeat).
# On clone et compile dans l'image pour éviter tout montage à l'exécution
# (contrairement à FLUKA qui nécessitait un mount propriétaire).
FROM of-base AS offbeat-build

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        cmake \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Source OpenFOAM puis compile OFFBEAT
# Remplacer l'URL par le dépôt officiel ou une copie interne.
ARG OFFBEAT_REPO=https://gitlab.com/offbeat-solver/offbeat.git
ARG OFFBEAT_BRANCH=main

RUN bash -c "source /usr/lib/openfoam/openfoam2406/etc/bashrc && \
    git clone --depth 1 --branch ${OFFBEAT_BRANCH} ${OFFBEAT_REPO} /opt/offbeat && \
    cd /opt/offbeat && \
    ./Allwmake -j$(nproc) 2>&1 | tee /opt/offbeat/build.log"

# ── Étape 3 : image finale ────────────────────────────────────────────────────
FROM of-base AS final

USER root

# Dépendances Python
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /autooffbeat

# Copier le binaire OFFBEAT depuis l'étape de build
COPY --from=offbeat-build /opt/offbeat/platforms /opt/offbeat/platforms
# Rendre le binaire accessible sur le PATH (chemin typique wmake)
RUN ln -sf /opt/offbeat/platforms/linux64GccDPInt32Opt/bin/offbeat /usr/local/bin/offbeat

# Installer les dépendances Python
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copier le code applicatif
COPY . .

# Créer les répertoires de volumes montables
RUN mkdir -p AutoOFFBEAT_logs offbeat_skills/templates offbeat_skills/examples

# Source OpenFOAM au démarrage du conteneur
ENV OPENFOAM_BASHRC=/usr/lib/openfoam/openfoam2406/etc/bashrc
ENV OFFBEAT_BIN=/usr/local/bin/offbeat

# Dash écoute sur 8000, mappé en 8050 par docker run -p 8050:8000
EXPOSE 8000

# Sourcer OpenFOAM puis lancer l'app
CMD ["bash", "-c", "source $OPENFOAM_BASHRC && python3 app.py"]
