#!/usr/bin/env python3

import numpy as np
import sys
import argparse
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchzq
from functools import partial
from pathlib import Path
from torch.utils.data import ConcatDataset
from torchvision import transforms
from torchvision.datasets import ImageFolder
from torchsummary import summary
from adabelief_pytorch import AdaBelief


sys.path.append(".")

from scripts.models.dcgan import Generator, Discriminator


class AugWrapper(nn.Module):
    def __init__(self, D, image_size, prob):
        super().__init__()
        self.D = D
        self.prob = prob

    @staticmethod
    def random_crop_and_resize(images, scale):
        size = images.shape[-1]

        new_size = int(size * scale)

        dsize = size - new_size

        h0 = int(np.random.random() * dsize)
        h1 = h0 + new_size

        w0 = int(np.random.random() * dsize)
        w1 = w0 + new_size

        cropped = images[..., h0:h1, w0:w1]
        cropped = cropped.clone()

        return F.interpolate(
            cropped,
            size=(size, size),
            mode="bilinear",
            align_corners=True,
        )

    @staticmethod
    def random_hflip(tensor, prob):
        if prob > np.random.random():
            return tensor
        return torch.flip(tensor, dims=(3,))

    def forward(self, images, detach=False):
        if np.random.random() < self.prob:
            random_scale = np.random.uniform(0.75, 0.95)
            images = self.random_hflip(images, prob=0.5)
            images = self.random_crop_and_resize(images, scale=random_scale)

        return self.D(images)


class Runner(torchzq.GANRunner):
    def __init__(
        self,
        root: Path = "data/processed",
        capacity: int = 16,
        zdim: int = 128,
        ds_repeat: int = 100,
        base_size: int = 144,
        crop_size: int = 128,
        aug_prob: float = 0.5,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.update_args(locals(), ["self", "kwargs"])

    @property
    def Optimizer(self):
        return partial(AdaBelief, eps=1e-12, betas=(0.5, 0.9))

    def create_dataset(self):
        dataset = self.autofeed(
            ImageFolder,
            dict(
                transform=transforms.Compose(
                    [
                        transforms.Resize(self.args.base_size),
                        transforms.RandomCrop(self.args.crop_size),
                        transforms.ToTensor(),
                        transforms.Normalize(0.5, 1),
                    ]
                )
            ),
        )
        return ConcatDataset([dataset] * self.args.ds_repeat)

    def create_model(self):
        G = self.autofeed(
            Generator,
            dict(nc=3),
            dict(
                image_size="crop_size",
                latent_dim="zdim",
                ngf="capacity",
            ),
        )

        self.last_generated = None

        def hook(m, i, o):
            self.last_generated = o

        G.register_forward_hook(hook)

        D = AugWrapper(
            self.autofeed(
                Discriminator,
                dict(nc=3),
                dict(image_size="crop_size", ndf="capacity"),
            ),
            self.args.crop_size,
            self.args.aug_prob,
        )

        model = nn.ModuleList([G, D])

        return model

    def prepare_batch(self, batch):
        x, _ = batch
        return x.to(self.args.device), None

    def sample(self, n):
        args = self.args
        z = torch.randn(n, args.zdim).to(args.device)
        z = F.normalize(z, dim=-1)
        return z

    def initialize(self):
        super().initialize()
        args = self.args

        if self.training:

            def plot(iteration):
                if iteration % args.plot_every == 0:
                    self.logger.add_images(
                        "generated",
                        (self.last_generated[:16] + 0.5).clamp(0, 1),
                    )
                    self.logger.render(iteration)

            self.events.iteration_completed.append(plot)

    @torchzq.command(inherit=True)
    def train(self, plot_every: int = 100, **kwargs):
        self.args.plot_every = plot_every
        super().train(**kwargs)


if __name__ == "__main__":
    torchzq.start(Runner)
