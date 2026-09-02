# TimeTrack shell logger — zdroj pravdy pro noční rekonciliaci.
#
# Loguje po každém příkazu: čas, pracovní adresář, příkaz.
# Z toho reconcile.py večer odhadne, kolik času jsi kde strávil, a porovná
# to se zapsanými záznamy.
#
# Instalace — přidej do ~/.bashrc:
#     source /CESTA/K/office/tools/shell-logger.sh
#
# (zsh: funguje taky, přidej do ~/.zshrc)

_TT_LOG_DIR="${TT_LOG_DIR:-$HOME/.local/share/timetrack}"
mkdir -p "$_TT_LOG_DIR" 2>/dev/null

_tt_prompt_log() {
  local ec=$?
  local last
  if [ -n "$ZSH_VERSION" ]; then
    last=$(fc -ln -1 2>/dev/null)
  else
    last=$(HISTTIMEFORMAT='' history 1 2>/dev/null | sed 's/^ *[0-9]\+ *//')
  fi
  # zahodit tabulátory a nové řádky v příkazu, ať zůstane jeden řádek na záznam
  last=${last//$'\t'/ }
  last=${last//$'\n'/ }
  printf '%s\t%s\t%s\n' "$(date -Iseconds)" "$PWD" "$last" >> "$_TT_LOG_DIR/shell.log"
  return $ec
}

if [ -n "$ZSH_VERSION" ]; then
  autoload -Uz add-zsh-hook 2>/dev/null && add-zsh-hook precmd _tt_prompt_log
else
  case "$PROMPT_COMMAND" in
    *_tt_prompt_log*) ;;
    *) PROMPT_COMMAND="_tt_prompt_log${PROMPT_COMMAND:+; $PROMPT_COMMAND}" ;;
  esac
fi
