"""Evidence-preserving project state and a single preliminary coordination plan."""
from __future__ import annotations

import copy
import json
from pathlib import Path

RULE_BASE = json.loads(Path(__file__).with_name("routing_rules.json").read_text(encoding="utf-8"))
RULES = RULE_BASE["rules"]
FACT_KEYS = tuple(rule["field"] for rule in RULES)
DETAIL_KEYS = ("existing_use", "proposed_use", "location", "alterations", "site_changes",
               "specialty_work", "existing_approvals", "project_description", "site_activity", "right_of_way_authority",
               "floor_area", "cooking_method", "construction_budget", "project_type", "exterior_work")
FACT_VALUES = {"yes", "no", "not_sure", "unknown"}


def empty_project() -> dict:
    return {"fields": {key: {"value": "unknown", "evidence": []} for key in (*DETAIL_KEYS, *FACT_KEYS)},
            "corrections": [], "conflicts": {}, "disclosed_fee_ids": [], "revision": 0}


def upgrade_project(previous: dict) -> dict:
    record = empty_project()
    record.update(copy.deepcopy(previous))
    record["fields"] = {**empty_project()["fields"], **record["fields"]}
    record["disclosed_fee_ids"] = [value for value in record.get("disclosed_fee_ids", []) if isinstance(value, str)]
    return record


def update_project(previous: dict, updates: object, statement: str) -> dict:
    """Only accept changes accompanied by an exact excerpt of the current applicant turn."""
    project = upgrade_project(previous)
    if not isinstance(updates, dict):
        raise ValueError("The advisor returned invalid project updates. Please retry.")
    for key, change in updates.items():
        if key not in project["fields"] or not isinstance(change, dict):
            continue
        value, evidence = change.get("value"), change.get("evidence")
        if not isinstance(value, str) or not isinstance(evidence, str):
            continue
        value, evidence = value.strip()[:1200], evidence.strip()[:1200]
        if not value or not evidence or evidence not in statement:
            continue
        if key in FACT_KEYS and value not in FACT_VALUES:
            continue
        # Silence is never a deletion of an earlier supported fact.
        if value == "unknown":
            continue
        old = project["fields"][key]
        differs = old["value"] != value and old["value"] != "unknown"
        explicit_correction = change.get("correction") is True
        if key in FACT_KEYS and differs and not explicit_correction:
            project["conflicts"][key] = {"previous": old["value"], "proposed": value, "evidence": evidence}
            continue
        if differs:
            project["corrections"].append({"field": key, "previous": old["value"], "value": value,
                                            "previous_evidence": old["evidence"], "evidence": evidence,
                                            "revision": project["revision"] + 1})
        if explicit_correction:
            project["conflicts"].pop(key, None)
        support = [] if differs else old["evidence"]
        project["fields"][key] = {"value": value, "evidence": list(dict.fromkeys([*support, evidence]))[-8:]}
    project["revision"] += 1
    return project


def routing_facts(project: dict) -> dict:
    return {key: "not_sure" if key in project["conflicts"] else project["fields"][key]["value"] for key in FACT_KEYS}


def intake_progress(project: dict, question_field: str = "", question_reason: str = "", plan_ready: bool = False) -> dict:
    """Describe evidence-backed topic coverage, independently of permit approval."""
    definitions = [
        ("project", "Your project", ("project_description", "project_type"), (), False),
        ("location", "Project location", ("location",), (), False),
        ("use", "Existing & proposed use", ("business_use", "existing_use", "proposed_use", "existing_approvals"), ("business_use",), False),
        ("building", "Building & plumbing", ("interior_work", "plumbing", "occupancy_change", "alterations", "floor_area", "construction_budget"), ("interior_work", "plumbing", "occupancy_change"), False),
        ("specialty", "Fire & specialty work", ("specialized_review", "specialty_work", "cooking_method"), ("specialized_review",), False),
        ("site", "Site & parking", ("site_work", "right_of_way", "site_changes", "site_activity", "right_of_way_authority"), ("site_work", "right_of_way"), False),
        ("historic", "Historic preservation", ("historic_review", "exterior_work"), ("historic_review",), True),
    ]
    topics = []
    fields = project["fields"]
    conflicts = project.get("conflicts", {})
    for identifier, title, keys, decisions, optional in definitions:
        values = [fields[key]["value"] for key in keys]
        if optional and fields["historic_review"]["value"] == "unknown" and question_field not in keys:
            continue
        active = question_field in keys
        uncertain = any(key in conflicts or fields[key]["value"] == "not_sure" for key in keys)
        if decisions:
            covered = all(fields[key]["value"] in {"yes", "no"} for key in decisions)
            if identifier == "use" and fields["business_use"]["value"] == "yes":
                covered = covered and all(fields[key]["value"] not in {"unknown", "not_sure", ""} for key in ("existing_use", "proposed_use"))
            excluded = all(fields[key]["value"] == "no" for key in decisions)
        else:
            covered = any(value not in {"unknown", "not_sure", ""} for value in values)
            excluded = False
        status = "active" if active else "uncertain" if uncertain else "excluded" if excluded else "covered" if covered else "partial" if any(value != "unknown" for value in values) else "pending"
        reason = question_reason if active else ""
        if plan_ready and status in {"partial", "pending", "uncertain"}:
            status = "deferred"
            explanations = {
                "project": "The project scope still needs confirmation.",
                "location": "Location has not been established; address-specific checks remain open.",
                "use": "Existing use, proposed use or use eligibility still needs confirmation.",
                "building": "Unresolved building, plumbing or occupancy details remain in the preliminary plan for City confirmation.",
                "specialty": "Equipment, trade or fire-review applicability has not been established.",
                "site": "Site, parking or street-access scope still needs confirmation.",
                "historic": "Historic status or exterior-work requirements still need confirmation.",
            }
            reason = explanations[identifier]
        topics.append({"id": identifier, "title": title, "status": status,
                       "reason": reason})
    return {"topics": topics, "covered": sum(topic["status"] in {"covered", "excluded"} for topic in topics), "total": len(topics)}


def project_summary(project: dict) -> str:
    fields = project["fields"]
    get = lambda key: fields[key]["value"] if fields[key]["value"] != "unknown" else ""
    description = get("project_description")
    if not description:
        existing, proposed = get("existing_use"), get("proposed_use")
        description = f"Proposed change from {existing} to {proposed}." if existing and proposed else (
            f"Proposed project: {proposed}." if proposed else "The project scope still needs clarification.")
    parts = [description]
    if get("location"):
        parts.append("Location: " + get("location") + ".")
    for key, label in (("alterations", "Proposed work"), ("site_changes", "Site scope"),
                       ("specialty_work", "Specialty scope"), ("existing_approvals", "Reported approvals")):
        value = get(key)
        if value and value.casefold() not in description.casefold():
            parts.append(label + ": " + value.rstrip(".") + ".")
    return " ".join(parts)


def build_brief(project: dict) -> dict:
    facts = routing_facts(project)
    steps = []
    # This is coordination order. It does not assert final COU issuance precedes building/fire approval.
    for rule in RULES:
        value = facts[rule["field"]]
        # Historic review begins only after the visitor or an official lookup identifies a historic context.
        if rule["id"] == "historic" and value == "unknown":
            continue
        status = "applicable" if value == "yes" else "not_applicable" if value == "no" else "unresolved"
        exception_note = ""
        site_activity = project["fields"]["site_activity"]["value"]
        authority = project["fields"]["right_of_way_authority"]["value"]
        if rule["id"] == "site" and site_activity in {"parking_restriping_only", "ada_only"}:
            status = "not_applicable"
            exception_note = "The reported work is limited to an exception to the parking/VUA trigger. Other permits may still need confirmation."
        if rule["id"] == "row" and authority in {"fdot", "private", "jea"}:
            status = "unresolved" if value != "no" else "not_applicable"
            exception_note = "A City ROW permit is not established for this jurisdiction. Development Services should identify the appropriate agency."
        if rule["id"] == "historic" and project["fields"]["exterior_work"]["value"] == "no":
            status = "not_applicable"
            exception_note = "The reported work is interior-only. Confirm with Historic Preservation if the scope changes to include exterior or site work."
        prerequisites = ["use"] if rule["id"] in {"occupancy", "building", "plumbing"} and facts["business_use"] != "no" else []
        title = rule["title"]
        if status == "unresolved":
            title = "Resolve whether to " + title[0].lower() + title[1:]
        steps.append({**rule, "status": status, "prerequisites": prerequisites,
                      "prerequisite_basis": "Preliminary use coordination before building submission; City COU Steps 1–2" if prerequisites else "Independent coordination; City confirms any formal dependencies",
                      "title": title, "evidence": project["fields"][rule["field"]]["evidence"],
                      "exception_note": exception_note,
                      "rule_version": RULE_BASE["version"]})
    # A changed commercial use may need a mobility decision even without exterior construction.
    if facts["business_use"] in {"yes", "not_sure"}:
        steps.append({"id":"mobility", "field":"business_use", "status":"unresolved",
                      "title":"Confirm mobility review, credits and any project-specific fee",
                      "department":"Concurrency and Mobility Management System Office (CMMSO)",
                      "request":"Could you confirm whether this use change needs mobility review, which existing-use credits or exemptions apply, and the appropriate calculation process?",
                      "outcome":"City determination of mobility applicability and any certified fee",
                      "prerequisites":[], "prerequisite_basis":"An early budget/feasibility conversation; City determines the formal sequence.",
                      "evidence":project["fields"]["business_use"]["evidence"],
                      "source_title":"Concurrency and Mobility Management System Office — applications and legacy rights",
                      "source_url":"https://www.jacksonville.gov/departments/planning-department/transportation-planning/concurrency-and-mobility-management-system-office",
                      "guidance":"CMMSO reviews mobility applications and legacy development rights. A change of use merits checking review needs, credits and exemptions even if there is no exterior work.",
                      "exceptions":"The calculation-certificate fee is not the actual mobility fee. No traffic rate, exemption or fee total has been established for this project.",
                      "exception_note":"", "rule_version":RULE_BASE["version"]})
    fee_ids = {"use":{"cou-new"}, "occupancy":{"alterations-bid-permit-fee","alterations-minimum-bid-permit-fee","plans-examination"},
               "building":{"alterations-bid-permit-fee","alterations-minimum-bid-permit-fee","plans-examination"},
               "mobility":{"mobility-certificate","mobility-de-minimis"}, "specialty":{"hood-review"}}
    for step in steps:
        step["fees"] = [row for row in project.get("research",{}).get("fees",[]) if row["id"] in fee_ids.get(step["id"],set())]
    active = [step for step in steps if step["status"] != "not_applicable"]
    conversations = []
    for step in active:
        group = next((item for item in conversations if item["group"] == step["department"]), None)
        if group is None:
            group = {"group": step["department"], "step_ids": [], "why": step["request"],
                     "bring": "The project description and any existing approval records.", "requests": []}
            conversations.append(group)
        group["step_ids"].append(step["id"])
        group["requests"].append(step["request"])
    groups = []
    for status, title in (("unresolved", "Decisions to confirm"), ("applicable", "Preliminary review routes")):
        selected = [step for step in active if step["status"] == status]
        if selected:
            groups.append({"outcome": "review-flag" if status == "unresolved" else "preliminary-route", "title": title,
                           "description": "City confirmation is needed before treating any route as established.",
                           "items": [{"item": step["title"], "why": "Applicant reported: " + "; ".join(step["evidence"]) if step["evidence"] else "This scope has not been established.",
                                      "source": step["source_title"], "source_url": step["source_url"], "step_id": step["id"]} for step in selected]})
    return {"project_summary": project_summary(project), "likely_route": "Resolve the open scope decisions with the relevant City team before formal submission.",
            "action_plan": steps, "city_conversations": conversations, "checklist_groups": groups,
            "notes": ["Reported approvals are applicant statements; they have not been verified."] if project["fields"]["existing_approvals"]["value"] != "unknown" else [],
            "rules": {key: RULE_BASE[key] for key in ("version", "review_status", "sources_checked")}}


def email_draft(project: dict, brief: dict, team: str, name: str = "", reply_email: str = "", location: str = "", note: str = "") -> dict:
    """A short scope paragraph, relevant suggestions and one coordinator-facing request."""
    active = [step for step in brief["action_plan"] if step["status"] != "not_applicable" and
              (team == "Overall City coordination" or step["department"] == team)]
    if not active:
        raise ValueError("There are no coordination steps for this department.")
    fields = project["fields"]
    value = lambda key: fields[key]["value"] if fields[key]["value"] != "unknown" else ""
    scope = value("project_description") or value("proposed_use") or "a project whose scope is still being developed"
    # Only the relevant scope is included; no checklist or agency-wide data dump.
    detail_keys = ("site_changes",) if team.startswith("Development") else ("alterations", "specialty_work")
    details = [value(key) for key in detail_keys if value(key) and value(key).casefold() not in scope.casefold()]
    def compact(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value.rstrip(".")
        return value[:limit].rsplit(" ", 1)[0].rstrip(".,;") + "…"
    paragraph = "My proposed project: " + compact(scope, 360) + "."
    if details:
        paragraph += " The proposed scope includes " + compact("; ".join(details), 220) + "."
    address = location or value("location")
    if address:
        paragraph += " Location: " + compact(address, 180) + "."
    suggested = [step["title"].removeprefix("Confirm ") for step in active if step["status"] == "applicable"]
    unresolved = [step["title"].removeprefix("Resolve whether to confirm ") for step in active if step["status"] == "unresolved"]
    suggestions = "Jax Project Advisor suggested checking " + ", ".join(suggested[:3]) + (" and other scope-dependent reviews" if len(suggested) > 3 else "") + ", subject to City confirmation." if suggested else "Jax Project Advisor identified scope decisions that need City confirmation."
    if unresolved:
        suggestions += " We still need confirmation of " + ", ".join(unresolved[:2]) + "."
    # Group by department; the email has one request, rather than one request per flag.
    request = active[0]["request"] if len(active) == 1 else (
        "Could you confirm the appropriate permit and review path for this scope, including the unresolved items above, and the documents we should prepare before submitting?"
        if team == "Overall City coordination" else
        "Could you confirm the reviews your department needs for this scope and the information we should prepare before submission?")
    paragraphs = ["Hello,", paragraph, suggestions, request]
    if note:
        paragraphs.append("Additional context: " + compact(note, 240))
    paragraphs.append("Thank you,\n" + (name or "[Your name]") + ("\n" + reply_email if reply_email else ""))
    proposed = value("proposed_use") or "project scope"
    return {"subject": "Preliminary permit routing — " + proposed[:80], "body": "\n\n".join(paragraphs), "team": team}
