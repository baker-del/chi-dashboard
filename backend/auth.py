import os
import yaml


def load_user_config():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "users.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def get_user_access(email: str) -> dict | None:
    """
    Return access info for the given email, or None if not authorized.

    Returns a dict with:
      role         — "all", "csm", or "subset"
      display_name — human-readable name
      hubspot_name — (csm only) owner name as it appears in HubSpot
      allowed_csms — (subset only) list of CSM names they can see
    """
    config = load_user_config()
    user = config.get("users", {}).get(email.lower())
    if not user:
        return None
    return {
        "role": user.get("role"),
        "display_name": user.get("display_name", email),
        "hubspot_name": user.get("hubspot_name"),
        "allowed_csms": user.get("allowed_csms", []),
    }


def allowed_csm_names(access: dict) -> list[str] | None:
    """
    Return the list of CSM names this user is allowed to see,
    or None if they have unrestricted access.
    """
    role = access["role"]
    if role == "all":
        return None
    if role == "csm":
        return [access["hubspot_name"]]
    if role == "subset":
        return access["allowed_csms"]
    return []
