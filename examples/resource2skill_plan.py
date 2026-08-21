from evoagent.integrations import Resource2SkillAdapter

spec = Resource2SkillAdapter(execution_enabled=False).build_spec(
    checkout_path="../external/Resource2Skill",
    domain="public-demo",
)
print("repository:", spec.repository_url)
print("validation command:", " ".join(spec.validation_command))
print("wiki path:", spec.skills_wiki_path)
print("library path:", spec.skills_library_path)
print("execution enabled:", spec.execution_enabled)
