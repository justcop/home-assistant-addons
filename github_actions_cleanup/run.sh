#!/usr/bin/with-contenv bashio
set -euo pipefail

REPOSITORY="$(bashio::config 'repository')"
TOKEN="$(bashio::config 'github_token')"
CONFIRM="$(bashio::config 'confirm_delete')"

if [[ -z "$TOKEN" ]]; then
  bashio::log.fatal "github_token is empty. Add a GitHub token with Actions write access."
  exit 1
fi

if [[ ! "$REPOSITORY" =~ ^[^/]+/[^/]+$ ]]; then
  bashio::log.fatal "repository must be in owner/repository format, for example justcop/glasto-switcher."
  exit 1
fi

API="https://api.github.com/repos/$REPOSITORY/actions/artifacts"
AUTH_HEADER="Authorization: Bearer $TOKEN"
ACCEPT_HEADER="Accept: application/vnd.github+json"
VERSION_HEADER="X-GitHub-Api-Version: 2022-11-28"

bashio::log.info "Scanning stored GitHub Actions artifacts for $REPOSITORY..."

page=1
count=0
bytes=0
artifact_ids=()
artifact_names=()

while true; do
  response="$(curl --fail-with-body -sS -L \
    -H "$AUTH_HEADER" \
    -H "$ACCEPT_HEADER" \
    -H "$VERSION_HEADER" \
    "$API?per_page=100&page=$page")"

  page_count="$(jq '.artifacts | length' <<<"$response")"
  [[ "$page_count" -eq 0 ]] && break

  while IFS=$'\t' read -r id name size expired; do
    [[ -z "$id" ]] && continue
    if [[ "$expired" == "false" ]]; then
      artifact_ids+=("$id")
      artifact_names+=("$name")
      ((count+=1))
      ((bytes+=size))
    fi
  done < <(jq -r '.artifacts[] | [.id, .name, .size_in_bytes, .expired] | @tsv' <<<"$response")

  [[ "$page_count" -lt 100 ]] && break
  ((page+=1))
done

mib="$(awk "BEGIN {printf \"%.1f\", $bytes/1024/1024}")"

if [[ "$count" -eq 0 ]]; then
  bashio::log.info "No active Actions artifacts found. Nothing to delete."
  exit 0
fi

bashio::log.info "Found $count artifact(s), using approximately $mib MiB."
for name in "${artifact_names[@]}"; do
  bashio::log.info "  - $name"
done

if [[ "$CONFIRM" != "true" ]]; then
  bashio::log.warning "Dry run only. Set confirm_delete to true and start the add-on again to delete these artifacts."
  exit 0
fi

bashio::log.warning "Deleting $count artifact(s) from $REPOSITORY..."

deleted=0
failed=0
for id in "${artifact_ids[@]}"; do
  status="$(curl -sS -L -o /tmp/delete-response -w "%{http_code}" -X DELETE \
    -H "$AUTH_HEADER" \
    -H "$ACCEPT_HEADER" \
    -H "$VERSION_HEADER" \
    "$API/$id")"

  if [[ "$status" == "204" ]]; then
    ((deleted+=1))
    bashio::log.info "Deleted artifact $id ($deleted/$count)"
  else
    ((failed+=1))
    bashio::log.error "Failed to delete artifact $id. GitHub returned HTTP $status."
    if [[ -s /tmp/delete-response ]]; then
      cat /tmp/delete-response
    fi
  fi
done

bashio::log.info "Cleanup complete. Deleted $deleted artifact(s); $failed failed."
bashio::log.info "GitHub billing storage can take some time to reflect reclaimed space."

[[ "$failed" -eq 0 ]]
