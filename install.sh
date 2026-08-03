#!/usr/bin/env bash
# Interactive local installer for the Astra Agent development stack.
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
INSTALL_DIR="${ASTRA_INSTALL_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/astra-agent}"
ENV_FILE="$INSTALL_DIR/astra-agent.env"
COMPOSE_FILE="$INSTALL_DIR/compose.yaml"
TUI_BIN_DIR="$INSTALL_DIR/bin"

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required"
}

prompt() {
  local question=$1 default=$2 value
  read -r -p "$question [$default]: " value
  printf '%s' "${value:-$default}"
}

prompt_secret() {
  local question=$1 value
  read -r -s -p "$question: " value
  printf '\n' >&2
  [ -n "$value" ] || die "a value is required"
  printf '%s' "$value"
}

confirm() {
  local answer
  answer=$(prompt "$1" "y")
  case "$answer" in
    y|Y|yes|YES) return 0 ;;
    n|N|no|NO) return 1 ;;
    *) die "please answer y or n" ;;
  esac
}

env_value() {
  local value=$1
  case "$value" in
    *$'\n'*|*$'\r'*) die "configuration values cannot contain line breaks" ;;
  esac
  # Compose and pydantic-settings both accept single-quoted dotenv values.
  value=${value//\'/\\\'}
  printf "'%s'" "$value"
}

write_env() {
  local key=$1 value=$2
  printf '%s=%s\n' "$key" "$(env_value "$value")" >> "$ENV_FILE"
}

require_command docker
require_command uv
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"

mkdir -p "$INSTALL_DIR" "$TUI_BIN_DIR"
chmod 700 "$INSTALL_DIR"

printf '%s\n' 'Astra Agent interactive installer'
printf 'Configuration directory: %s\n\n' "$INSTALL_DIR"

controller_mode=$(prompt 'Run the controller locally or in Docker? (local/docker)' 'docker')
case "$controller_mode" in
  local|docker) ;;
  *) die "controller mode must be local or docker" ;;
esac

if confirm 'Use an existing MariaDB instance?'; then
  db_url=$(prompt 'MariaDB SQLAlchemy URL reachable by the controller' 'mysql+aiomysql://astra_agent:password@127.0.0.1:3306/astra_agent?charset=utf8mb4')
  use_mariadb=false
else
  mariadb_password=$(prompt_secret 'New MariaDB root password')
  use_mariadb=true
  if [ "$controller_mode" = docker ]; then
    db_host=mariadb
  else
    db_host=127.0.0.1
  fi
  db_url="mysql+aiomysql://root:${mariadb_password}@${db_host}:3306/astra_agent?charset=utf8mb4"
fi

if confirm 'Configure OpenRouter as the conversation provider?'; then
  openrouter_key=$(prompt_secret 'OpenRouter API key')
  openrouter_model=$(prompt 'OpenRouter model' 'openai/gpt-4.1-mini')
  model_backend=openrouter
else
  openrouter_key=''
  openrouter_model='openai/gpt-4.1-mini'
  model_backend=development
fi

if [ "$controller_mode" = docker ]; then
  qdrant_url=http://qdrant:6333
else
  qdrant_url=http://127.0.0.1:6333
fi

umask 077
: > "$ENV_FILE"
write_env ASTRA_ENVIRONMENT development
write_env ASTRA_PERSISTENCE_BACKEND mariadb
write_env ASTRA_DATABASE_URL "$db_url"
write_env ASTRA_AUTH_BACKEND development_headers
write_env ASTRA_USER_AUTH_BACKEND development_headers
write_env ASTRA_MODEL_BACKEND "$model_backend"
write_env ASTRA_OPENROUTER_API_KEY "$openrouter_key"
write_env ASTRA_OPENROUTER_MODEL "$openrouter_model"
write_env ASTRA_MEMORY_BACKEND qdrant
write_env ASTRA_QDRANT_URL "$qdrant_url"
write_env ASTRA_QDRANT_COLLECTION astra_memories
write_env ASTRA_MEMORY_EXTRACTOR_BACKEND deterministic
write_env ASTRA_CONTEXT_COMPRESSOR_BACKEND deterministic
if [ "$use_mariadb" = true ]; then
  write_env MARIADB_ROOT_PASSWORD "$mariadb_password"
fi
chmod 600 "$ENV_FILE"

cat > "$COMPOSE_FILE" <<EOF
name: astra-agent
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "127.0.0.1:6333:6333"
    volumes:
      - qdrant-data:/qdrant/storage
EOF

if [ "$use_mariadb" = true ]; then
  cat >> "$COMPOSE_FILE" <<'EOF'
  mariadb:
    image: mariadb:11.8
    environment:
      MARIADB_DATABASE: astra_agent
      MARIADB_ROOT_PASSWORD: ${MARIADB_ROOT_PASSWORD}
    ports:
      - "127.0.0.1:3306:3306"
    volumes:
      - mariadb-data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 5s
      timeout: 3s
      retries: 20
EOF
fi

if [ "$controller_mode" = docker ]; then
  cat >> "$COMPOSE_FILE" <<EOF
  controller:
    build:
      context: $(env_value "$ROOT_DIR")
    command: ["/bin/sh", "-c", "alembic upgrade head && exec uvicorn astra_agent.app:create_app --factory --host 0.0.0.0 --port 8000"]
    env_file:
      - $ENV_FILE
    ports:
      - "127.0.0.1:8000:8000"
EOF
  if [ "$use_mariadb" = true ]; then
    cat >> "$COMPOSE_FILE" <<'EOF'
    depends_on:
      mariadb:
        condition: service_healthy
EOF
  fi
fi

cat >> "$COMPOSE_FILE" <<'EOF'
volumes:
  qdrant-data:
EOF
if [ "$use_mariadb" = true ]; then
  printf '  mariadb-data:\n' >> "$COMPOSE_FILE"
fi

# A user-owned uv tool remains updateable, while /usr/bin provides the requested stable commands.
if [ "${ASTRA_SKIP_TUI_INSTALL:-false}" != true ]; then
  UV_TOOL_BIN_DIR="$TUI_BIN_DIR" uv tool install --editable --force "$ROOT_DIR/apps/astra-tui"
  sudo install -d -m 755 /usr/bin
  sudo ln -sfn "$TUI_BIN_DIR/astra-tui" /usr/bin/astra-tui
  sudo ln -sfn /usr/bin/astra-tui /usr/bin/agent-tui
fi

tui_install_note='The TUI is installed as /usr/bin/astra-tui (also available as /usr/bin/agent-tui).'
if [ "${ASTRA_SKIP_TUI_INSTALL:-false}" = true ]; then
  tui_install_note='TUI installation was skipped.'
fi

if [ "${ASTRA_SKIP_START:-false}" != true ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build
fi

if [ "$controller_mode" = local ]; then
  uv sync --all-packages --directory "$ROOT_DIR"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  if [ "${ASTRA_SKIP_START:-false}" != true ]; then
    (cd "$ROOT_DIR" && uv run alembic upgrade head)
  fi
  cat <<EOF

Qdrant and MariaDB are running in Docker. Start the local controller with:
  set -a; . "$ENV_FILE"; set +a
  cd "$ROOT_DIR" && uv run astra-agent
EOF
else
  cat <<EOF

The Docker controller is running at http://127.0.0.1:8000.
EOF
fi

cat <<EOF
Qdrant is running in Docker at http://127.0.0.1:6333.
$tui_install_note
Configuration: $ENV_FILE
Manage containers: docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" <command>
EOF
