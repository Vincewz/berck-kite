#!/usr/bin/env python3
"""Purge les anciens déploiements Vercel d'un projet.

Le stockage des déploiements Vercel compte la somme de tous les déploiements
conservés. Comme le bot commite plusieurs fois par jour, il faut régulièrement
supprimer les déploiements obsolètes en gardant seulement les plus récents.

Usage :
    # Simulation (par défaut) : affiche ce qui serait supprimé
    VERCEL_TOKEN=xxx python3 scripts/vercel_prune_deployments.py --project berck-kite

    # Suppression réelle, en conservant les 5 déploiements les plus récents
    VERCEL_TOKEN=xxx python3 scripts/vercel_prune_deployments.py \
        --project berck-kite --keep 5 --yes
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.vercel.com"


def api_request(path: str, token: str, method: str = "GET", params: dict | None = None):
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    request = urllib.request.Request(url, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code} : {detail}") from exc
    if not body:
        return None
    import json

    return json.loads(body)


def find_project(token: str, name: str) -> tuple[str, str | None, str]:
    """Renvoie (project_id, team_id, project_name)."""
    project = api_request(f"/v9/projects/{urllib.parse.quote(name)}", token)
    return project["id"], project.get("accountId"), project["name"]


def list_deployments(token: str, project_id: str, team_id: str | None) -> list[dict]:
    deployments: list[dict] = []
    until: int | None = None
    while True:
        payload = api_request(
            "/v6/deployments",
            token,
            params={"projectId": project_id, "teamId": team_id, "limit": 100, "until": until},
        )
        batch = payload.get("deployments", [])
        if not batch:
            break
        deployments.extend(batch)
        if len(batch) < 100:
            break
        until = batch[-1]["created"]
    return deployments


def format_date(ms: int | None) -> str:
    if not ms:
        return "?"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge des anciens déploiements Vercel")
    parser.add_argument("--project", required=True, help="Nom du projet Vercel")
    parser.add_argument("--keep", type=int, default=5, help="Déploiements récents à conserver")
    parser.add_argument("--token", default=os.getenv("VERCEL_TOKEN"), help="Token Vercel (ou VERCEL_TOKEN)")
    parser.add_argument("--yes", action="store_true", help="Supprimer réellement (sinon simulation)")
    parser.add_argument("--pause", type=float, default=0.35, help="Pause entre deux suppressions (s)")
    args = parser.parse_args()

    if not args.token:
        print("Token Vercel manquant : passe --token ou définis VERCEL_TOKEN.", file=sys.stderr)
        return 2

    project_id, team_id, project_name = find_project(args.token, args.project)
    print(f"Projet {project_name} ({project_id}) — équipe {team_id or 'compte personnel'}")

    deployments = list_deployments(args.token, project_id, team_id)
    print(f"{len(deployments)} déploiements trouvés")

    if args.keep >= len(deployments):
        print("Rien à supprimer.")
        return 0

    doomed = deployments
    if args.keep > 0:
        doomed = deployments[args.keep:]

    print(f"\nConservation des {args.keep} plus récents :")
    for deployment in deployments[: args.keep]:
        print(f"  gardé   {format_date(deployment.get('created'))}  {deployment.get('url')}")

    print(f"\n{len(doomed)} déploiements à supprimer :")
    for deployment in doomed[:10]:
        print(f"  purge   {format_date(deployment.get('created'))}  {deployment.get('url')}")
    if len(doomed) > 10:
        print(f"  … et {len(doomed) - 10} autres")

    if not args.yes:
        print("\nSimulation : relance avec --yes pour supprimer réellement.")
        return 0

    print()
    deleted = 0
    failures = 0
    for deployment in doomed:
        try:
            api_request(
                f"/v13/deployments/{deployment['uid']}",
                args.token,
                method="DELETE",
                params={"teamId": team_id},
            )
            deleted += 1
            if deleted % 25 == 0:
                print(f"  {deleted}/{len(doomed)} supprimés…")
        except RuntimeError as exc:
            failures += 1
            print(f"  échec {deployment['uid']}: {exc}", file=sys.stderr)
        time.sleep(args.pause)

    print(f"\nTerminé : {deleted} supprimés, {failures} échecs")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
