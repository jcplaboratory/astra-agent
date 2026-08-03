#!/usr/bin/env bash
# Interactive production installer for Astra Agent.
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
INSTALL_DIR="${ASTRA_INSTALL_DIR:-/etc/astra-agent}"
ENV_FILE="$INSTALL_DIR/astra-agent.env"
COMPOSE_FILE="$INSTALL_DIR/compose.yaml"
TUI_BIN_DIR="$INSTALL_DIR/bin"

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

prompt() {
  local question=$1 default=$2 value
  read -r -p "$question [$default]: " value
  printf '%s' "${value:-$default}"
}

prompt_required() {
  local question=$1 value
  read -r -p "$question: " value
  [ -n "$value" ] || die "a value is required"
  printf '%s' "$value"
}

prompt_secret() {
  local question=$1 value
  read -r -s -p "$question: " value
  printf '\n' >&2
  [ -n "$value" ] || die "a value is required"
  printf '%s' "$value"
}

choose() {
  local question=$1 default=$2 allowed=$3 value
  value=$(prompt "$question ($allowed)" "$default")
  case " $allowed " in *" $value "*) printf '%s' "$value" ;; *) die "expected one of: $allowed" ;; esac
}

confirm() {
  case "$(choose "$1" y 'y n')" in y) return 0 ;; n) return 1 ;; esac
}

env_value() {
  local value=$1
  case "$value" in *$'\n'*|*$'\r'*) die "configuration values cannot contain line breaks" ;; esac
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "$value"
}

write_env() { printf '%s=%s\n' "$1" "$(env_value "$2")" >> "$ENV_FILE"; }

require_command docker
require_command uv
UV_BIN=$(command -v uv)
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"

if [ "$(id -u)" -ne 0 ] && [ "${ASTRA_TEST_MODE:-false}" != true ]; then
  die "run this production installer as root so it can secure /etc and install /usr/bin/astra"
fi

install -d -m 700 "$INSTALL_DIR" "$TUI_BIN_DIR"
umask 077

printf '%s\n' 'Astra Agent production installer'
printf '%s\n\n' 'All controller configuration is collected below. Credentials are stored in a root-only environment file.'

controller_mode=$(choose 'Run the controller' docker 'local docker')
controller_port=$(prompt 'Private controller HTTP port' '8000')
public_agent_url=$(prompt_required 'User-facing HTTPS API URL for astra')
ingress_port=$(prompt 'Public ARA mTLS ingress port' '8443')
tls_dir=$(prompt_required 'Directory containing server.crt, server.key, and ca.crt for ARA ingress')
[ -r "$tls_dir/server.crt" ] && [ -r "$tls_dir/server.key" ] && [ -r "$tls_dir/ca.crt" ] || die "TLS directory must contain readable server.crt, server.key, and ca.crt"

if confirm 'Use an existing MariaDB instance?'; then
  database_url=$(prompt_required 'MariaDB SQLAlchemy URL reachable by the controller')
  use_mariadb=false
else
  mariadb_database=$(prompt 'MariaDB database name' 'astra_agent')
  mariadb_root_password=$(prompt_secret 'New MariaDB root password')
  mariadb_user=$(prompt 'MariaDB application user' 'astra_agent')
  mariadb_password=$(prompt_secret 'New MariaDB application password')
  use_mariadb=true
  if [ "$controller_mode" = docker ]; then database_host=mariadb; else database_host=127.0.0.1; fi
  database_url="mysql+aiomysql://${mariadb_user}:${mariadb_password}@${database_host}:3306/${mariadb_database}?charset=utf8mb4"
fi

openrouter_api_key=$(prompt_secret 'OpenRouter API key')
openrouter_model=$(prompt 'OpenRouter model' 'openai/gpt-4.1-mini')
openrouter_base_url=$(prompt 'OpenRouter API base URL' 'https://openrouter.ai/api/v1')
persona_kernel=$(prompt 'Persona kernel' 'Be direct, accurate, and transparent. Preserve user control.')

keycloak_url=$(prompt_required 'Keycloak public base URL')
keycloak_realm=$(prompt 'Keycloak realm' 'astra-agent')
keycloak_issuer_default="${keycloak_url%/}/realms/${keycloak_realm}"
oidc_issuer=$(prompt 'Keycloak issuer URL' "$keycloak_issuer_default")
oidc_audience=$(prompt 'Keycloak client audience' 'astra-agent')
oidc_jwks_url=$(prompt 'Keycloak JWKS URL' "${oidc_issuer%/}/protocol/openid-connect/certs")
oidc_tenant_claim=$(prompt 'Keycloak tenant UUID claim' 'tenant_id')
oidc_jwks_cache_seconds=$(prompt 'OIDC JWKS cache seconds' '300')
trusted_proxy_secret=$(prompt_secret 'New trusted mTLS proxy shared secret')

if confirm 'Configure S3-compatible artifact storage?'; then
  artifact_bucket=$(prompt_required 'Artifact bucket')
  artifact_endpoint_url=$(prompt 'S3 endpoint URL (blank for AWS)' '')
  artifact_access_key=$(prompt_secret 'S3 access key')
  artifact_secret_key=$(prompt_secret 'S3 secret key')
  artifact_region=$(prompt 'S3 region' 'us-east-1')
else
  artifact_bucket=''; artifact_endpoint_url=''; artifact_access_key=''; artifact_secret_key=''; artifact_region='us-east-1'
fi

qdrant_collection=$(prompt 'Qdrant collection' 'astra_memories')
qdrant_api_key=$(prompt 'Qdrant API key (blank for local container)' '')
if [ "$controller_mode" = docker ]; then qdrant_url=http://qdrant:6333; else qdrant_url=http://127.0.0.1:6333; fi

memory_extractor_backend=$(choose 'Memory extractor backend' deterministic 'deterministic local_model')
context_compressor_backend=$(choose 'Context compressor backend' deterministic 'deterministic local_model')
local_model_url=$(prompt 'Local OpenAI-compatible model URL' 'http://127.0.0.1:11434/v1')
local_model_name=$(prompt 'Local model name' 'qwen2.5:3b')
local_model_api_key=$(prompt 'Local model API key (blank if none)' '')

lease_duration_seconds=$(prompt 'ARA lease duration seconds' '60')
persona_max_tokens=$(prompt 'Persona/context maximum tokens' '800')
conversation_history_messages=$(prompt 'Conversation history messages' '12')
memory_max_records=$(prompt 'Maximum memories per turn' '8')
delegation_enabled=$(choose 'Enable ARA delegation' true 'true false')
delegation_max_siblings=$(prompt 'Maximum sibling ARAs per task' '2')
delegation_wait_seconds=$(prompt 'Delegation wait seconds' '30')
delegation_poll_seconds=$(prompt 'Delegation poll seconds' '0.25')
tenant_workspaces=$(prompt 'Tenant workspaces JSON map' '{}')
tool_read_max_bytes=$(prompt 'Tool read maximum bytes' '65536')
tool_search_max_file_bytes=$(prompt 'Tool search maximum file bytes' '65536')
tool_search_max_files=$(prompt 'Tool search maximum files' '200')
tool_search_max_matches=$(prompt 'Tool search maximum matches' '100')
tool_search_max_output_bytes=$(prompt 'Tool search maximum output bytes' '65536')
sandbox_executable=$(prompt 'Absolute Docker/Podman executable for sandbox (blank to disable)' '')
sandbox_image=$(prompt 'Sandbox image (blank to disable)' '')
[ -z "$sandbox_executable" ] && [ -n "$sandbox_image" ] && die "sandbox executable is required with a sandbox image"
[ -n "$sandbox_executable" ] && [ -z "$sandbox_image" ] && die "sandbox image is required with a sandbox executable"
sandbox_timeout_seconds=$(prompt 'Sandbox timeout seconds' '30')
sandbox_max_output_bytes=$(prompt 'Sandbox maximum output bytes' '65536')
sandbox_memory_limit=$(prompt 'Sandbox memory limit' '512m')
sandbox_cpu_limit=$(prompt 'Sandbox CPU limit' '1')
sandbox_pids_limit=$(prompt 'Sandbox PID limit' '128')

: > "$ENV_FILE"
write_env ASTRA_ENVIRONMENT production
write_env ASTRA_PERSISTENCE_BACKEND mariadb
write_env ASTRA_DATABASE_URL "$database_url"
write_env ASTRA_CREATE_SCHEMA_ON_STARTUP false
write_env ASTRA_LEASE_DURATION_SECONDS "$lease_duration_seconds"
write_env ASTRA_AUTH_BACKEND mtls
write_env ASTRA_TRUSTED_PROXY_SECRET "$trusted_proxy_secret"
write_env ASTRA_USER_AUTH_BACKEND oidc
write_env ASTRA_OIDC_ISSUER "$oidc_issuer"
write_env ASTRA_OIDC_AUDIENCE "$oidc_audience"
write_env ASTRA_OIDC_JWKS_URL "$oidc_jwks_url"
write_env ASTRA_OIDC_TENANT_CLAIM "$oidc_tenant_claim"
write_env ASTRA_OIDC_JWKS_CACHE_SECONDS "$oidc_jwks_cache_seconds"
write_env ASTRA_ARTIFACT_BUCKET "$artifact_bucket"
write_env ASTRA_ARTIFACT_ENDPOINT_URL "$artifact_endpoint_url"
write_env ASTRA_ARTIFACT_ACCESS_KEY "$artifact_access_key"
write_env ASTRA_ARTIFACT_SECRET_KEY "$artifact_secret_key"
write_env ASTRA_ARTIFACT_REGION "$artifact_region"
write_env ASTRA_MODEL_BACKEND openrouter
write_env ASTRA_OPENROUTER_API_KEY "$openrouter_api_key"
write_env ASTRA_OPENROUTER_MODEL "$openrouter_model"
write_env ASTRA_OPENROUTER_BASE_URL "$openrouter_base_url"
write_env ASTRA_PERSONA_KERNEL "$persona_kernel"
write_env ASTRA_PERSONA_MAX_TOKENS "$persona_max_tokens"
write_env ASTRA_CONVERSATION_HISTORY_MESSAGES "$conversation_history_messages"
write_env ASTRA_MEMORY_BACKEND qdrant
write_env ASTRA_MEMORY_MAX_RECORDS "$memory_max_records"
write_env ASTRA_QDRANT_URL "$qdrant_url"
write_env ASTRA_QDRANT_API_KEY "$qdrant_api_key"
write_env ASTRA_QDRANT_COLLECTION "$qdrant_collection"
write_env ASTRA_MEMORY_EXTRACTOR_BACKEND "$memory_extractor_backend"
write_env ASTRA_CONTEXT_COMPRESSOR_BACKEND "$context_compressor_backend"
write_env ASTRA_LOCAL_MODEL_URL "$local_model_url"
write_env ASTRA_LOCAL_MODEL_NAME "$local_model_name"
write_env ASTRA_LOCAL_MODEL_API_KEY "$local_model_api_key"
write_env ASTRA_DELEGATION_ENABLED "$delegation_enabled"
write_env ASTRA_DELEGATION_MAX_SIBLINGS "$delegation_max_siblings"
write_env ASTRA_DELEGATION_WAIT_SECONDS "$delegation_wait_seconds"
write_env ASTRA_DELEGATION_POLL_SECONDS "$delegation_poll_seconds"
write_env ASTRA_TENANT_WORKSPACES "$tenant_workspaces"
write_env ASTRA_TOOL_READ_MAX_BYTES "$tool_read_max_bytes"
write_env ASTRA_TOOL_SEARCH_MAX_FILE_BYTES "$tool_search_max_file_bytes"
write_env ASTRA_TOOL_SEARCH_MAX_FILES "$tool_search_max_files"
write_env ASTRA_TOOL_SEARCH_MAX_MATCHES "$tool_search_max_matches"
write_env ASTRA_TOOL_SEARCH_MAX_OUTPUT_BYTES "$tool_search_max_output_bytes"
write_env ASTRA_SANDBOX_EXECUTABLE "$sandbox_executable"
write_env ASTRA_SANDBOX_IMAGE "$sandbox_image"
write_env ASTRA_SANDBOX_TIMEOUT_SECONDS "$sandbox_timeout_seconds"
write_env ASTRA_SANDBOX_MAX_OUTPUT_BYTES "$sandbox_max_output_bytes"
write_env ASTRA_SANDBOX_MEMORY_LIMIT "$sandbox_memory_limit"
write_env ASTRA_SANDBOX_CPU_LIMIT "$sandbox_cpu_limit"
write_env ASTRA_SANDBOX_PIDS_LIMIT "$sandbox_pids_limit"
if [ "$use_mariadb" = true ]; then
  write_env MARIADB_DATABASE "$mariadb_database"
  write_env MARIADB_ROOT_PASSWORD "$mariadb_root_password"
  write_env MARIADB_USER "$mariadb_user"
  write_env MARIADB_PASSWORD "$mariadb_password"
fi
chmod 600 "$ENV_FILE"

if [ "$controller_mode" = docker ]; then controller_upstream=controller:"$controller_port"; else controller_upstream=host.docker.internal:"$controller_port"; fi
cat > "$COMPOSE_FILE" <<EOF
name: astra-agent
services:
  qdrant:
    image: qdrant/qdrant:latest
    restart: unless-stopped
    volumes:
      - qdrant-data:/qdrant/storage
  ara-ingress:
    image: nginx:1.27-alpine
    restart: unless-stopped
    ports:
      - "${ingress_port}:8443"
    environment:
      ASTRA_CONTROLLER_UPSTREAM: ${controller_upstream}
      ASTRA_TRUSTED_PROXY_SECRET: \${ASTRA_TRUSTED_PROXY_SECRET}
    volumes:
      - ${ROOT_DIR}/deploy/nginx-mtls.conf.template:/etc/nginx/templates/default.conf.template:ro
      - ${tls_dir}:/etc/nginx/tls:ro
EOF
if [ "$controller_mode" = local ]; then printf '    extra_hosts:\n      - "host.docker.internal:host-gateway"\n' >> "$COMPOSE_FILE"; fi
if [ "$use_mariadb" = true ]; then
  cat >> "$COMPOSE_FILE" <<'EOF'
  mariadb:
    image: mariadb:11.8
    restart: unless-stopped
    environment:
      MARIADB_DATABASE: ${MARIADB_DATABASE}
      MARIADB_ROOT_PASSWORD: ${MARIADB_ROOT_PASSWORD}
      MARIADB_USER: ${MARIADB_USER}
      MARIADB_PASSWORD: ${MARIADB_PASSWORD}
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
  migrate:
    build:
      context: ${ROOT_DIR}
    command: ["alembic", "upgrade", "head"]
    env_file: [${ENV_FILE}]
    restart: "no"
EOF
  if [ "$use_mariadb" = true ]; then cat >> "$COMPOSE_FILE" <<'EOF'
    depends_on:
      mariadb:
        condition: service_healthy
EOF
  fi
  cat >> "$COMPOSE_FILE" <<EOF
  controller:
    build:
      context: ${ROOT_DIR}
    command: ["uvicorn", "astra_agent.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "${controller_port}"]
    env_file: [${ENV_FILE}]
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
EOF
fi
cat >> "$COMPOSE_FILE" <<'EOF'
volumes:
  qdrant-data:
EOF
if [ "$use_mariadb" = true ]; then printf '  mariadb-data:\n' >> "$COMPOSE_FILE"; fi
chmod 600 "$COMPOSE_FILE"

if [ "${ASTRA_SKIP_TUI_INSTALL:-false}" != true ]; then
  UV_TOOL_BIN_DIR="$TUI_BIN_DIR" uv tool install --editable --force "$ROOT_DIR/apps/astra-tui"
  cat > /usr/bin/astra <<EOF
#!/usr/bin/env sh
ASTRA_AGENT_URL=$(env_value "$public_agent_url")
export ASTRA_AGENT_URL
exec $(env_value "$TUI_BIN_DIR/astra-tui") "\$@"
EOF
  chmod 755 /usr/bin/astra
fi

tui_install_note='astra is installed at /usr/bin/astra'
if [ "${ASTRA_SKIP_TUI_INSTALL:-false}" = true ]; then tui_install_note='astra installation was skipped'; fi

if [ "${ASTRA_SKIP_START:-false}" != true ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build
fi
if [ "$controller_mode" = local ]; then
  uv sync --all-packages --directory "$ROOT_DIR"
  if [ "${ASTRA_SKIP_START:-false}" != true ]; then
    (cd "$ROOT_DIR" && uv run --env-file "$ENV_FILE" alembic upgrade head)
    cat > /etc/systemd/system/astra-agent.service <<EOF
[Unit]
Description=Astra Agent controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
ExecStart=$UV_BIN run --env-file $ENV_FILE uvicorn astra_agent.app:create_app --factory --host 0.0.0.0 --port $controller_port
Restart=on-failure
RestartSec=5
User=root
Group=root

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now astra-agent.service
  fi
  cat <<EOF

The locally installed controller is managed by systemd:
  systemctl status astra-agent.service
EOF
fi
cat <<EOF

Installed production configuration: $ENV_FILE
Generated deployment definition: $COMPOSE_FILE
$tui_install_note and configured for: $public_agent_url
ARA ingress is listening on port $ingress_port. Place the controller behind a separate user-facing HTTPS gateway.
EOF
