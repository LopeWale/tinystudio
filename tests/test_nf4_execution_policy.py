"""Explicit NF4 execution choices, independent of runtime version whitelists."""
import importlib.util

import pytest


def policy():
    assert importlib.util.find_spec("tinystudio.training.nf4_execution") is not None, "NF4 execution policy is missing"
    from tinystudio.training.nf4_execution import NF4ExecutionPolicy
    return NF4ExecutionPolicy


@pytest.mark.parametrize("value", [{"cpu_inference": "guessed"}, {"hidden_fallback": True}, [], "portable"])
def test_execution_policy_requires_explicit_known_choices(value):
    with pytest.raises(ValueError):
        policy().resolve(value)


def model():
    import torch
    from bitsandbytes.nn import Linear4bit
    return torch.nn.Sequential(Linear4bit(16, 16, bias=False, quant_type="nf4"),
                               Linear4bit(16, 16, bias=False, quant_type="nf4"))


def test_library_default_is_observed_without_reconfiguration():
    network = model()
    before = [layer.support_avx512bf16_for_cpu for layer in network]
    receipt = policy().resolve(None).apply(network, "cpu")
    assert [layer.support_avx512bf16_for_cpu for layer in network] == before
    assert receipt["policy"] == {"cpu_inference": "library_default"}
    assert [row["before"] for row in receipt["modules"]] == before
    assert all(not row["changed"] for row in receipt["modules"])
    assert receipt["qualified"] is False


def test_portable_cpu_path_is_applied_only_when_selected():
    network = model()
    for layer in network:
        layer.support_avx512bf16_for_cpu = True
    receipt = policy().resolve({"cpu_inference": "portable"}).apply(network, "cpu")
    assert all(layer.support_avx512bf16_for_cpu is False for layer in network)
    assert all(row["before"] is True and row["after"] is False and row["changed"] for row in receipt["modules"])
    assert receipt["qualified"] is False


def test_cpu_policy_does_not_redirect_an_accelerator_selection():
    network = model()
    for layer in network:
        layer.support_avx512bf16_for_cpu = True
    receipt = policy().resolve({"cpu_inference": "portable"}).apply(network, "cuda:1")
    assert all(layer.support_avx512bf16_for_cpu is True for layer in network)
    assert receipt["applicable"] is False


def test_unsupported_portable_feature_fails_before_partial_application():
    network = model()
    network[0].support_avx512bf16_for_cpu = True
    del network[1].support_avx512bf16_for_cpu
    with pytest.raises(ValueError, match="portable.*unavailable|feature"):
        policy().resolve({"cpu_inference": "portable"}).apply(network, "cpu")
    assert network[0].support_avx512bf16_for_cpu is True


@pytest.mark.parametrize('strategy', ['full', 'lora'])
def test_matched_nonquantized_controls_can_retain_inactive_policy_metadata(strategy):
    from tinystudio.training.train import TrainConfig
    config = TrainConfig(base_model='local-control', update_strategy=strategy,
                         nf4_execution={'cpu_inference': 'portable'})
    recorded = config.as_dict()
    assert recorded['nf4_execution'] == {'cpu_inference': 'portable'}
    assert recorded['nf4_execution_applicable'] is False
