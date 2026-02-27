import concurrent.futures

def first_import_in_bg():
    import torch
    return torch.get_num_threads()

with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(first_import_in_bg)
    bg_threads = future.result()

print("Background init threads:", bg_threads)
