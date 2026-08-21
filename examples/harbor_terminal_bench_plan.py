from evoagent.integrations import HarborCLIAdapter

spec = HarborCLIAdapter(execution_enabled=False).build_run(
    agent="evoagent",
    model="provider/model",
    workspace="./.evoagent/harbor-runs/development",
    trials_per_task=1,
    concurrency=2,
    required_environment_variables=("MODEL_API_KEY",),
)
print("dataset:", spec.dataset_ref)
print("command:", " ".join(spec.command))
print("upload:", spec.upload)
print("public:", spec.public)
print("execution enabled:", spec.execution_enabled)
