CLUSTER  ?= llm
ENV_FILE ?= .env
YAML     ?= sky.yaml

# Source .env so $(LLM_HOSTNAME) etc. are available to recipes. Make's
# `include` keeps surrounding quotes as part of the value — SkyPilot's
# --env-file parser strips them. Normalize here so both agree. (Affects
# LLM_HOSTNAME / LLM_API_KEY used by `health`.)
ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
LLM_HOSTNAME := $(patsubst "%",%,$(patsubst '%',%,$(LLM_HOSTNAME)))
LLM_API_KEY  := $(patsubst "%",%,$(patsubst '%',%,$(LLM_API_KEY)))
endif

.PHONY: help up down status logs health cost check budget

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*##/ { printf "  %-10s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

up: $(ENV_FILE)  ## Launch the GPU cluster and start vLLM + tunnel (override: YAML=sky-big.yaml)
	sky launch -c $(CLUSTER) -y $(YAML) --env-file $(ENV_FILE) \
		--idle-minutes-to-autostop 30 --down

down:  ## Terminate the cluster (stops billing)
	sky down -y $(CLUSTER)

status:  ## Show cluster status
	sky status $(CLUSTER)

logs:  ## Tail vLLM + cloudflared logs from the cluster
	sky logs $(CLUSTER)

health:  ## Hit the public endpoint and check it's alive
	@test -n "$(LLM_HOSTNAME)" || (echo "LLM_HOSTNAME not set in .env"; exit 1)
	@test -n "$(LLM_API_KEY)"  || (echo "LLM_API_KEY not set in .env";  exit 1)
	@curl -sf "https://$(LLM_HOSTNAME)/v1/models" \
		-H "Authorization: Bearer $(LLM_API_KEY)" \
		&& echo "" && echo "OK" \
		|| (echo "FAIL — tunnel or llama-server not responding"; exit 1)

cost:  ## Show SkyPilot cost report
	sky cost-report

check:  ## Verify SkyPilot can talk to RunPod
	sky check runpod

budget:  ## Run the monthly budget guard once
	bash scripts/budget-check.sh

$(ENV_FILE):
	@echo ".env not found."
	@echo "  cp .env.example .env"
	@echo "  # then edit .env to fill in values"
	@exit 1
