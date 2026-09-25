"""Explicit device selection for the PyTorch plate detector and EasyOCR."""
import os
from functools import lru_cache


@lru_cache(maxsize=4)
def resolve(requested):
    if requested not in {'cpu', 'auto', 'cuda', 'mps'}:
        raise ValueError('デバイスはcpu/auto/cuda/mpsを指定してください。')
    if requested == 'cpu':
        return 'cpu'
    import torch
    cuda = torch.cuda.is_available()
    mps = hasattr(torch.backends, 'mps') and torch.backends.mps.is_available()
    if requested == 'auto':
        return 'cuda' if cuda else 'mps' if mps else 'cpu'
    if (requested == 'cuda' and not cuda) or (requested == 'mps' and not mps):
        raise ValueError(f'{requested}を利用できません。Mac DockerではMPSは使えません。Mac直接起動かCPUを選択してください。')
    return requested


def torch_device():
    return resolve(os.getenv('GATE_INFERENCE_DEVICE', 'cpu'))


def synchronize(device):
    if device != 'cpu':
        import torch
        if device == 'cuda':
            torch.cuda.synchronize()
        elif device == 'mps':
            torch.mps.synchronize()
