# Techloop Sim - Migrazione PostgreSQL

## 📋 Indice
1. [Cosa è cambiato](#cosa-è-cambiato)
2. [Prima installazione](#prima-installazione)
3. [Migrazione da SQLite esistente](#migrazione-da-sqlite-esistente)
4. [Gestione database](#gestione-database)
5. [Backup e ripristino](#backup-e-ripristino)
6. [Troubleshooting](#troubleshooting)

---

## 🔄 Cosa è cambiato

### Servizi aggiunti
- **PostgreSQL 15** - Database principale
- **pgAdmin** (opzionale) - Interfaccia web gestione DB

### File modificati
- `docker-compose.yml` - Aggiunto PostgreSQL + dipendenze
- `configuration.yaml` - Configurato recorder con PostgreSQL
- `automations.yaml` - Nessuna modifica necessaria

### Nuovi file
```
postgres/
├── data/                    # Dati PostgreSQL (auto-generato)
├── init/
│   └── 01-init-homeassistant.sql  # Script inizializzazione
└── config/
    └── postgresql.conf      # Configurazione custom

backups/
└── postgres/                # Directory backup automatici

backup-postgres.sh           # Script backup
restore-postgres.sh          # Script ripristino
```

---

## 🚀 Prima installazione (nuovo sistema)

### 1. Ferma tutti i container
```bash
docker-compose down
```

### 2. Crea le directory necessarie
```bash
mkdir -p postgres/data postgres/init postgres/config
mkdir -p backups/postgres
mkdir -p pgadmin
```

### 3. Copia i file di configurazione
```bash
# Copia postgresql.conf (già fornito)
# Copia 01-init-homeassistant.sql (già fornito)
```

### 4. Rendi eseguibili gli script
```bash
chmod +x backup-postgres.sh restore-postgres.sh
```

### 5. Avvia il sistema
```bash
docker-compose up -d
```

### 6. Verifica PostgreSQL
```bash
# Controlla che PostgreSQL sia healthy
docker ps

# Verifica connessione
docker exec -it techloop-postgres psql -U ha_user -d homeassistant -c "\dt"
```

### 7. Verifica Home Assistant
```bash
# Attendi 30-60 secondi che HA crei le tabelle
# Accedi a http://localhost:8123

# Verifica log
docker logs techloop-homeassistant | grep -i postgres
```

**Dovrebbe mostrare:**
```
INFO (Recorder) Connected to PostgreSQL
```

---

## 📦 Migrazione da SQLite esistente

### Opzione A: Ripartire da zero (CONSIGLIATO)

Il metodo più pulito è ripartire con database vuoto:

```bash
# 1. Ferma tutto
docker-compose down

# 2. Backup del vecchio SQLite (opzionale)
cp homeassistant/home-assistant_v2.db homeassistant/home-assistant_v2.db.backup

# 3. Elimina vecchio database SQLite
rm homeassistant/home-assistant_v2.db

# 4. Avvia con PostgreSQL
docker-compose up -d
```

### Opzione B: Migrazione con tool (più complessa)

Se vuoi mantenere lo storico:

```bash
# 1. Installa sqlite3-to-postgres
pip3 install sqlite3-to-postgres

# 2. Ferma Home Assistant
docker stop techloop-homeassistant

# 3. Esegui migrazione
sqlite3-to-postgres \
  --sqlite-file homeassistant/home-assistant_v2.db \
  --postgres-host localhost \
  --postgres-port 5432 \
  --postgres-database homeassistant \
  --postgres-user ha_user \
  --postgres-password techloop_secure_2026

# 4. Riavvia Home Assistant
docker start techloop-homeassistant
```

⚠️ **Nota**: La migrazione può richiedere ore per database grandi (>1GB)

---

## 🛠️ Gestione database

### Accesso via terminale
```bash
# Connessione psql
docker exec -it techloop-postgres psql -U ha_user -d homeassistant

# Comandi utili:
\dt              # Lista tabelle
\d+ states       # Dettagli tabella states
\l               # Lista database
\q               # Esci
```

### Accesso via pgAdmin (opzionale)

1. **Attiva pgAdmin:**
```bash
docker-compose --profile admin up -d pgadmin
```

2. **Accedi a:** http://localhost:5050
   - Email: `admin@techloop.local`
   - Password: `admin_techloop_2026`

3. **Aggiungi server PostgreSQL:**
   - Host: `postgres`
   - Port: `5432`
   - Database: `homeassistant`
   - Username: `ha_user`
   - Password: `techloop_secure_2026`

### Query utili

```sql
-- Dimensione database
SELECT pg_size_pretty(pg_database_size('homeassistant'));

-- Statistiche tabelle
SELECT * FROM get_db_stats();

-- Record per entità
SELECT entity_id, COUNT(*) 
FROM states 
GROUP BY entity_id 
ORDER BY COUNT(*) DESC 
LIMIT 10;

-- Pulizia manuale >30 giorni
SELECT * FROM cleanup_old_states(30);

-- Vacuum manuale
VACUUM ANALYZE;
```

---

## 💾 Backup e ripristino

### Backup automatico

```bash
# Esegui backup manuale
./backup-postgres.sh

# Output:
# ✅ Backup completato: ./backups/postgres/ha_backup_20260128_153045.sql.gz (12M)
```

### Backup automatico giornaliero (cron)

```bash
# Apri crontab
crontab -e

# Aggiungi questa riga (backup ogni giorno alle 3:00)
0 3 * * * cd /path/to/techloop-sim && ./backup-postgres.sh >> /var/log/techloop-backup.log 2>&1
```

### Ripristino backup

```bash
# Lista backup disponibili
ls -lh backups/postgres/

# Ripristina backup specifico
./restore-postgres.sh backups/postgres/ha_backup_20260128_153045.sql.gz

# Conferma digitando: yes
```

⚠️ **ATTENZIONE**: Il ripristino cancella TUTTI i dati attuali!

---

## 📊 Monitoring

### Sensori database in Home Assistant

Dopo l'avvio, avrai questi sensori:

- `sensor.database_size_mb` - Dimensione DB in MB
- `sensor.database_states_count` - Numero record in tabella states
- `sensor.database_events_count` - Numero eventi registrati
- `sensor.database_oldest_record` - Età record più vecchio (giorni)

### Verifica performance

```bash
# Log query lente (>1s)
docker logs techloop-postgres | grep "duration:"

# Statistiche autovacuum
docker exec -it techloop-postgres psql -U ha_user -d homeassistant \
  -c "SELECT * FROM pg_stat_user_tables WHERE relname='states';"
```

---

## 🐛 Troubleshooting

### PostgreSQL non si avvia

```bash
# Verifica log
docker logs techloop-postgres

# Permessi directory
sudo chown -R 999:999 postgres/data

# Riavvia
docker-compose restart postgres
```

### Home Assistant non si connette

```bash
# Verifica log HA
docker logs techloop-homeassistant | grep -i error

# Testa connessione manualmente
docker exec -it techloop-postgres psql -U ha_user -d homeassistant -c "SELECT 1;"

# Verifica password in configuration.yaml
# db_url: postgresql://ha_user:techloop_secure_2026@postgres:5432/homeassistant
```

### Database troppo grande

```bash
# Riduci retention
# Modifica configuration.yaml:
# purge_keep_days: 7  # invece di 30

# Riavvia HA
docker-compose restart homeassistant

# Attendi purge automatico (ogni notte) o forza:
docker exec -it techloop-postgres psql -U ha_user -d homeassistant \
  -c "SELECT * FROM cleanup_old_states(7);"
```

### Performance lente

```bash
# 1. Verifica indici
docker exec -it techloop-postgres psql -U ha_user -d homeassistant \
  -c "SELECT schemaname, tablename, indexname FROM pg_indexes WHERE schemaname='public';"

# 2. Rigenera statistiche
docker exec -it techloop-postgres psql -U ha_user -d homeassistant \
  -c "ANALYZE;"

# 3. Verifica query lente nei log
docker logs techloop-postgres | grep "duration: [0-9][0-9][0-9][0-9]"
```

### Migrazione fallita

```bash
# 1. Elimina tutto e riparte da zero
docker-compose down -v
rm -rf postgres/data/*

# 2. Ricrea
docker-compose up -d postgres

# 3. Attendi inizializzazione (30s)
docker logs -f techloop-postgres

# 4. Riavvia HA
docker-compose up -d homeassistant
```

---

## 🔐 Sicurezza

### Cambia password di default

1. **Modifica docker-compose.yml:**
```yaml
POSTGRES_PASSWORD: tua_password_sicura_qui
```

2. **Modifica configuration.yaml:**
```yaml
db_url: postgresql://ha_user:tua_password_sicura_qui@postgres:5432/homeassistant
```

3. **Riavvia:**
```bash
docker-compose down
docker-compose up -d
```

### Backup cifrato

```bash
# Backup con cifratura GPG
./backup-postgres.sh
gpg --symmetric --cipher-algo AES256 backups/postgres/ha_backup_*.sql.gz
```

---

## 📈 Vantaggi PostgreSQL vs SQLite

| Feature | SQLite | PostgreSQL |
|---------|--------|------------|
| **Performance query** | ⚠️ Media | ✅ Eccellente |
| **Scritture concorrenti** | ❌ Lock | ✅ MVCC |
| **Backup online** | ❌ Difficile | ✅ Facile |
| **Indici avanzati** | ⚠️ Limitati | ✅ Completi |
| **Scalabilità** | ❌ Limitata | ✅ Ottima |
| **Dimensione max** | ~140 TB | ♾️ Illimitato |
| **Autovacuum** | ❌ Manuale | ✅ Automatico |

---

## 📞 Supporto

Per problemi o domande:
1. Controlla [Troubleshooting](#troubleshooting)
2. Verifica log: `docker logs techloop-postgres`
3. Verifica log HA: `docker logs techloop-homeassistant`

---

## 🎯 Checklist post-migrazione

- [ ] PostgreSQL avviato e healthy
- [ ] Home Assistant connesso a PostgreSQL
- [ ] Sensori OTGW funzionanti
- [ ] AI engine funzionante
- [ ] Backup automatico configurato
- [ ] Sensori database visibili in HA
- [ ] Grafici storici funzionanti
- [ ] Performance soddisfacenti

---

**✅ Migrazione completata con successo!** 🎉