import concurrent.futures
import sys

def test_bg():
    import torch
    return torch.get_num_threads()

with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(test_bg)
    bg_threads = future.result()

print("Background init threads:", bg_threads)

import torch
print("Main thread threads after bg init:", torch.get_num_threads())
