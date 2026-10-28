"""Linux ARM64 CPU NF4 qualification using the real HF/PEFT/TRL path.

Run ``python -m unittest tests.test_lab_qlora -v`` in the pinned optional pack.
An unavailable pack is explicitly skipped, never recorded as qualification.
"""
import gc
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tinystudio.training.quantization import cpu_pack_compatible


class LegacyQuantizedBoundaries(unittest.TestCase):
    def test_remote_running_and_failed_checkpoints_use_durable_marker_before_hydration(self):
        from types import SimpleNamespace
        from tinystudio.core.project import ProjectError
        from tinystudio.services.models import _resolve_experiment
        from tinystudio.training.quantization import POLICY_FORMAT
        for state in ("running", "failed"):
            for location in ("config", "status"):
                with self.subTest(state=state, location=location):
                    config, status = {"base_model": "original-float-base"}, {}
                    (config if location == "config" else status)["quantization_format"] = POLICY_FORMAT
                    exp = SimpleNamespace(config=config, status=status, state=state, alias="fixture")
                    with patch("tinystudio.services.models.ensure_experiment_artifacts", side_effect=AssertionError("hydrated unsupported checkpoint")) as hydrate:
                        with self.assertRaisesRegex(ProjectError, "retained base.*compatible target"):
                            _resolve_experiment(exp, "fixture/1@5", "5")
                        hydrate.assert_not_called()

    def test_retained_quantized_parent_cannot_reuse_merged_float_cache(self):
        from types import SimpleNamespace
        from tinystudio.core.project import ProjectError
        from tinystudio.services.jobs import parent_weights
        from tinystudio.training.quantization import POLICY_FORMAT
        exp = SimpleNamespace(config={"quantization_format": POLICY_FORMAT}, status={}, state="succeeded")
        with patch("tinystudio.services.jobs.find_experiment", return_value=exp), patch(
                "tinystudio.services.jobs.ensure_experiment_artifacts", side_effect=AssertionError("hydrated float cache")) as hydrate:
            with self.assertRaisesRegex(ProjectError, "refinement.*not qualified"):
                parent_weights(None, None, "fixture/1")
            hydrate.assert_not_called()

    def test_legacy_serving_cannot_substitute_original_float_base(self):
        from types import SimpleNamespace
        from tinystudio.core.project import ProjectError
        from tinystudio.services.models import _resolve_experiment
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "quantized-base").mkdir()
            (root / "adapter").mkdir()
            exp = SimpleNamespace(status={"quantization": {"format": "tinystudio.nf4-base.v1"}},
                config={"base_model": "original-float-base"}, state="succeeded", alias="fixture")
            with patch("tinystudio.services.models.ensure_experiment_artifacts", return_value=root):
                with self.assertRaisesRegex(ProjectError, "retained base.*compatible target"):
                    _resolve_experiment(exp, "fixture/1", "")

    def test_legacy_merged_export_rejects_before_loading_float_base(self):
        from tinystudio.core.project import ProjectError
        from tinystudio.training.train import export_merged
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "quantized-base").mkdir()
            with patch("transformers.AutoModelForCausalLM.from_pretrained", side_effect=AssertionError("loaded float base")) as loader:
                with self.assertRaisesRegex(ProjectError, "merged export is not qualified"):
                    export_merged(root, root / "merged", "original-float-base")
                loader.assert_not_called()

    def test_missing_base_does_not_enable_float_export(self):
        from tinystudio.core.project import ProjectError
        from tinystudio.training.train import export_merged
        for marker in ("nf4-policy.json", "pretrained.json"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / marker).write_text(json.dumps({"format": "tinystudio.pretrained.v2", "output_kind": "quantized_adapter"}))
                (root / "adapter").mkdir()
                with patch("transformers.AutoModelForCausalLM.from_pretrained", side_effect=AssertionError("loaded float base")) as loader:
                    with self.assertRaisesRegex(ProjectError, "merged export is not qualified"):
                        export_merged(root, root / "merged", "original-float-base")
                    loader.assert_not_called()


@unittest.skipUnless(cpu_pack_compatible(), "requires the pinned Linux ARM64 CPU NF4 pack")
class QLoRAQualification(unittest.TestCase):
    def setUp(self):
        import torch
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import LlamaConfig, LlamaForCausalLM, PreTrainedTokenizerFast
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        tokens = ["<pad>", "<eos>", "<unk>", "user", "assistant", ":", "pressure", "normal", "high", "low", "yes", "no", "sensor", "ready"]
        backend = Tokenizer(models.WordLevel({token: index for index, token in enumerate(tokens)}, unk_token="<unk>"))
        backend.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, eos_token="<eos>", pad_token="<pad>", unk_token="<unk>")
        tokenizer.chat_template = "{% for message in messages %}{{ message['role'] + ': ' + message['content'] + ' ' }}{% if message['role'] == 'assistant' %}{{ eos_token }}{% endif %}{% endfor %}"
        tokenizer.save_pretrained(self.source)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(31)
            LlamaForCausalLM(LlamaConfig(vocab_size=len(tokens), hidden_size=16, intermediate_size=32,
                num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=2, max_position_embeddings=64,
                attention_dropout=0, bos_token_id=1, eos_token_id=1, pad_token_id=0)).save_pretrained(self.source)
        self.archive = self.root / "source.tar"
        with tarfile.open(self.archive, "w") as bundle:
            for file in self.source.iterdir():
                bundle.add(file, arcname=file.name)
        self.model_hash = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def specification(self, objective):
        snapshot = self.root / f"{objective}.jsonl"
        rows = []
        for index in range(5):
            common = {"source_record_id": f"source:{index}",
                      "partition": "train" if index < 3 else "validation" if index == 3 else "test"}
            row = {"text": "  pressure -1.25 V\nμA\t"} if objective == "causal_lm" else {
                "messages": [{"role": "user", "content": "pressure high"}, {"role": "assistant", "content": "yes"}], "rejected": "no"}
            rows.append({**common, **row})
        snapshot.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        return {"definition": {"seed": 42, "learning": {"initialization": "pretrained", "objective": objective,
            "update_strategy": "qlora", "device": "cpu", "dtype": "float32"},
            "parameters": {"steps": 3, "context": 32, "learning_rate": 0.03, "batch_size": 1,
                "lora_r": 2, "lora_alpha": 4, "lora_dropout": 0, "lora_targets": ["q_proj", "v_proj"]},
            "inputs": [{"artifact_id": "model", "role": "pretrained_model", "sha256": self.model_hash},
                       {"artifact_id": "data", "role": "snapshot", "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}]},
            "input_paths": {"model": str(self.archive), "data": str(snapshot)}}

    def qualify(self, objective):
        import torch
        from bitsandbytes.nn import Linear4bit
        from peft.tuners.lora.layer import LoraLayer
        from trl import DPOTrainer, SFTTrainer
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
        from tinystudio.lab.compatibility import execute_pretrained, load_pretrained_output, extract_model_archive
        from tinystudio.training.quantization import verify_probes, state_digest
        output = self.root / "output"
        trainer_type = DPOTrainer if objective == "dpo" else SFTTrainer
        original_train = trainer_type.train
        observed = {"reference_during_training": 0, "fitting": False, "reference": False}
        live = []
        original_ref = DPOTrainer.compute_ref_log_probs

        def reference(trainer, *args, **kwargs):
            observed["reference"] = True
            try:
                return original_ref(trainer, *args, **kwargs)
            finally:
                observed["reference"] = False

        def train(trainer, *args, **kwargs):
            model = trainer.model
            frozen = {name: value.detach().clone() for name, value in model.state_dict().items() if "lora_" not in name}
            trainable = {name: value.detach().clone() for name, value in model.named_parameters() if value.requires_grad}
            self.assertTrue(trainable)
            self.assertTrue(all("lora_" in name and value.dtype == torch.float32 for name, value in trainable.items()))
            modules = [module for module in model.modules() if isinstance(module, Linear4bit)]
            self.assertEqual(len(modules), 7)
            self.assertTrue(all(module.weight.dtype == torch.uint8 and module.weight.quant_state.nested for module in modules))
            def before_forward(module, inputs):
                adapters = [child for child in module.modules() if isinstance(child, LoraLayer)]
                if observed["reference"]:
                    self.assertTrue(adapters and all(child.disable_adapters for child in adapters))
                # TRL 1.16 inlines reference forwards inside _compute_loss;
                # compute_ref_log_probs is used separately for precomputation.
                # Observe actual module context instead of assuming that helper
                # is the training call site.
                if observed["fitting"] and adapters and all(child.disable_adapters for child in adapters):
                    self.assertFalse(torch.is_grad_enabled())
                    observed["reference_during_training"] += 1
            hook = model.register_forward_pre_hook(before_forward)
            if objective == "dpo":
                batch = trainer._prepare_inputs(next(iter(trainer.get_train_dataloader())))
                before = tuple(value.detach().clone() for value in trainer.compute_ref_log_probs(model, batch))
            observed["fitting"] = True
            try:
                result = original_train(trainer, *args, **kwargs)
            finally:
                observed["fitting"] = False
            self.assertTrue(any(not torch.equal(value, dict(model.named_parameters())[name]) for name, value in trainable.items()))
            self.assertTrue(all(torch.equal(value, model.state_dict()[name]) for name, value in frozen.items()))
            optimizer = {id(parameter) for group in trainer.optimizer.param_groups for parameter in group["params"]}
            self.assertEqual(optimizer, {id(parameter) for parameter in model.parameters() if parameter.requires_grad})
            if objective == "dpo":
                after = trainer.compute_ref_log_probs(model, batch)
                self.assertTrue(all(torch.equal(left, right) for left, right in zip(before, after)))
                self.assertGreater(observed["reference_during_training"], 0)
            hook.remove()
            tokenizer = AutoTokenizer.from_pretrained(self.source, local_files_only=True)
            model.eval()
            for prompt in ("pressure normal", "-1.25 V", "sensor ready"):
                encoded = {name: value for name, value in tokenizer(prompt, return_tensors="pt", add_special_tokens=False).items()
                           if name in {"input_ids", "attention_mask"}}
                with torch.no_grad():
                    live.append((encoded, model(**encoded).logits[0, -1].detach().clone()))
            return result

        with patch.object(trainer_type, "train", train), patch.object(DPOTrainer, "compute_ref_log_probs", reference):
            result = execute_pretrained(self.specification(objective), output, lambda row: None, lambda: False)
        self.assertTrue(result["qualification"]["live_policy_reload_equal"])
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), self.model_hash)
        self.assertEqual(result["qualification"]["excluded_final_test_records"], 1)
        manifest = json.loads((output / "pretrained.json").read_text())
        self.assertEqual(manifest["format"], "tinystudio.pretrained.v2")
        self.assertEqual(manifest["output_kind"], "quantized_adapter")
        self.assertTrue(manifest["quantization"]["precision"]["post_trainer_precision_corrections"])
        extracted = extract_model_archive(output / "pretrained-model.tar", self.root / "extracted")
        restored, _, _ = load_pretrained_output(extracted)
        for encoded, expected in live:
            with torch.no_grad():
                self.assertTrue(torch.equal(expected, restored(**encoded).logits[0, -1]))
        self.qualify_serving(output, live)
        # A quantization-state mutation must fail the frozen-base identity oracle.
        old = state_digest(restored, base_only=True)
        module = next(child for child in restored.modules() if isinstance(child, Linear4bit))
        saved = module.weight.quant_state.absmax.clone()
        module.weight.quant_state.absmax.reshape(-1)[0] += 1
        self.assertNotEqual(state_digest(restored, base_only=True), old)
        module.weight.quant_state.absmax.copy_(saved)
        del restored
        gc.collect()
        # Reusing the original float base is a real violating counterexample.
        wrong = PeftModel.from_pretrained(AutoModelForCausalLM.from_pretrained(self.source, local_files_only=True),
                                         extracted / "adapter", local_files_only=True).eval()
        with self.assertRaisesRegex(ValueError, "differs from the live trained policy"):
            verify_probes(wrong, extracted, manifest["reload_probes"], "cpu")
        del wrong
        # Missing/corrupt retained state cannot enter the legacy float-base branch.
        path = extracted / "pretrained.json"
        missing = {**manifest}
        missing.pop("quantization")
        path.write_text(json.dumps(missing))
        with self.assertRaisesRegex(ValueError, "requires a retained NF4"):
            load_pretrained_output(extracted)
        path.write_text(json.dumps(manifest))
        weights = next((extracted / "quantized-base").glob("*.safetensors"))
        weights.write_bytes(weights.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            load_pretrained_output(extracted)

    def qualify_serving(self, output, live):
        """Real NF4 policy through the same retained source and chat runner as the API."""
        import torch
        from bitsandbytes.nn import Linear4bit
        from tinystudio.serving.retained import RetainedModelSource
        from tinystudio.serving.runner import ModelRunner, ResolvedModel
        from tinystudio.storage import ArtifactStorage, project_key
        from tinystudio.core.project import ProjectError

        bundle = output / "pretrained-model.tar"
        sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
        key = project_key("a" * 32, "lab", sha[:2], sha)
        store = ArtifactStorage(root=self.root / "serving-objects")
        store.upload_file(bundle, key)
        source = RetainedModelSource("a" * 32, "b" * 32, sha, bundle.stat().st_size, key)
        resolved = ResolvedModel("lab:" + "b" * 32, "never-the-float-base", None, None, "retained", source)
        opened = []
        original = RetainedModelSource.open

        @contextmanager
        def observe(binding):
            with original(binding) as root:
                opened.append(root)
                yield root

        with patch("tinystudio.serving.retained.storage", return_value=store), patch.object(
                RetainedModelSource, "open", observe), patch.dict("os.environ", {
                    "TINYSTUDIO_DEVICE": "cpu", "TINYSTUDIO_DTYPE": "float32"}):
            runner = ModelRunner(resolved)
            self.assertTrue(opened and all(not path.exists() for path in opened))
            modules = [module for module in runner.model.modules() if isinstance(module, Linear4bit)]
            self.assertEqual(len(modules), 7)
            self.assertTrue(all(module.weight.dtype == torch.uint8 and module.weight.quant_state.nested for module in modules))
            for encoded, expected in live:
                with torch.no_grad():
                    self.assertTrue(torch.equal(expected, runner.model(**encoded).logits[0, -1]))
            messages = [{"role": "user", "content": "pressure high"}]
            generated = runner.generate(messages, max_new_tokens=3, temperature=0)
            streamed = "".join(runner.stream(messages, max_new_tokens=3, temperature=0))
            self.assertEqual(streamed.strip(), generated["content"])
            prompt, completion = runner.count_tokens(messages, generated["content"])
            self.assertEqual(prompt, generated["prompt_tokens"])
            self.assertGreaterEqual(completion, 0)
            runner.unload()
            with self.assertRaisesRegex(ProjectError, "unloaded"):
                runner.generate(messages, max_new_tokens=3)
            reloaded = ModelRunner(resolved)
            self.assertEqual(reloaded.generate(messages, max_new_tokens=3)["content"], generated["content"])
            self.assertTrue(all(not path.exists() for path in opened))
            reloaded.unload()

    def test_sft_qlora(self):
        self.qualify("sft")

    def test_raw_pretraining_qlora(self):
        self.qualify("causal_lm")

    def test_dpo_qlora(self):
        self.qualify("dpo")

    def test_cancel_after_retention_removes_quantized_artifacts(self):
        from tinystudio.lab.compatibility import execute_pretrained
        output = self.root / "cancelled"
        result = execute_pretrained(self.specification("sft"), output, lambda row: None,
            lambda: (output / "quantized-base").exists())
        self.assertEqual(result["qualification"]["status"], "cancelled")
        self.assertFalse(result["artifacts"])
        self.assertFalse((output / "quantized-base").exists())
        self.assertFalse((output / "reload-probes.safetensors").exists())
        self.assertFalse((output / "nf4-policy.json").exists())


if __name__ == "__main__":
    unittest.main()
