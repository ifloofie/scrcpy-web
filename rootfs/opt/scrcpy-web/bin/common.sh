# shellcheck shell=sh
# Shared helpers for scrcpy-web shell scripts. Source, don't execute.

: "${DATA_DIR:=/data}"
: "${RUN_DIR:=/run/scrcpy-web}"
: "${ADB_PORT:=5037}"
export DATA_DIR RUN_DIR ADB_PORT
export ANDROID_ADB_SERVER_PORT="$ADB_PORT"

log() {
  printf '[%s] %s\n' "${LOG_NAME:-scrcpy-web}" "$*"
}

# is_true VALUE -> success for 1/true/yes/on (any case)
is_true() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

# read_secret NAME -> prints $NAME, or the contents of the file in $NAME_FILE
# (Docker secrets style). Prints nothing if neither is set.
read_secret() {
  eval "_val=\${$1:-}"
  eval "_file=\${$1_FILE:-}"
  if [ -n "$_val" ]; then
    printf '%s' "$_val"
  elif [ -n "$_file" ]; then
    if [ -r "$_file" ]; then
      # strip a single trailing newline
      printf '%s' "$(cat "$_file")"
    else
      log "WARNING: $1_FILE=$_file is not readable" >&2
    fi
  fi
}

random_token() {
  # URL-safe random string of the given length (default 24)
  python3 -c "import secrets,sys; print(secrets.token_urlsafe(64)[:int(sys.argv[1])])" "${1:-24}"
}
