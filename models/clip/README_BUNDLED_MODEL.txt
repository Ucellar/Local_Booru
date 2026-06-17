Local Booru bundled NO_MATCH visual model folder.

Full AI release builds must include CLIP-compatible model files here so users do not have to search or download a model manually.

Expected HuggingFace Transformers CLIP layout example:
- config.json
- preprocessor_config.json
- tokenizer.json / vocab.json / merges.txt
- model.safetensors or pytorch_model.bin

This source zip contains only the loader and folder contract. It does not contain model weights.
