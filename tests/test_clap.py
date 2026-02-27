import laion_clap
try:
    print("Imported laion_clap successfully.")
    print("Instantiating CLAP Module...")
    model = laion_clap.CLAP_Module(enable_fusion=False)
    # The load_ckpt method will attempt to download the default model if not provided
    print("Loading checkpoint...")
    model.load_ckpt()
    print("CLAP model instantiated and checkpoint loaded successfully!")
except Exception as e:
    print(f"Failed to instantiate CLAP model: {e}")
