#!/bin/bash

#############################################
# Techloop Sim - Setup Directory Structure
#############################################

echo "========================================"
echo "Techloop Sim - Setup Directory"
echo "========================================"
echo ""

# Crea directory PostgreSQL
echo "📁 Creazione directory PostgreSQL..."
mkdir -p postgres/data
mkdir -p postgres/init
mkdir -p postgres/config
touch postgres/data/.gitkeep

# Crea directory backup
echo "📁 Creazione directory backup..."
mkdir -p backups/postgres
touch backups/postgres/.gitkeep

# Crea directory pgAdmin
echo "📁 Creazione directory pgAdmin..."
mkdir -p pgadmin
touch pgadmin/.gitkeep

# Crea directory Mosquitto (se non esistono)
echo "📁 Verifica directory Mosquitto..."
mkdir -p mosquitto/config
mkdir -p mosquitto/data
mkdir -p mosquitto/log
touch mosquitto/data/.gitkeep
touch mosquitto/log/.gitkeep

# Crea directory AI Engine
echo "📁 Verifica directory AI Engine..."
mkdir -p ai-engine/data

# Permessi corretti per PostgreSQL
echo "🔐 Impostazione permessi PostgreSQL..."
chmod 700 postgres/data 2>/dev/null || true
chmod 755 postgres/init 2>/dev/null || true
chmod 755 postgres/config 2>/dev/null || true

# Permessi script
echo "🔐 Impostazione permessi script..."
chmod +x backup-postgres.sh 2>/dev/null || true
chmod +x restore-postgres.sh 2>/dev/null || true

# Crea .env se non esiste
if [ ! -f .env ]; then
    echo "📝 Creazione file .env..."
    cp .env.example .env
    echo "⚠️  Ricordati di modificare le password in .env!"
else
    echo "ℹ️  File .env già esistente (non sovrascritto)"
fi

echo ""
echo "========================================"
echo "✅ Setup completato!"
echo "========================================"
echo ""
echo "Struttura creata:"
tree -L 2 -d . 2>/dev/null || find . -type d -maxdepth 2 | sort

echo ""
echo "📋 Prossimi passi:"
echo "1. Verifica/modifica .env (IMPORTANTE: cambia password!)"
echo "2. docker-compose up -d"
echo "3. Attendi 30-60 secondi per inizializzazione DB"
echo "4. Accedi a http://localhost:8123"
echo ""