import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import MultiheadAttention

from utils import shift_dim

class GroupNorm3d(nn.Module):
    def __init__(self, channels):
        super(GroupNorm3d, self).__init__()
        self.gn = nn.GroupNorm(num_groups=32, num_channels=channels, eps=1e-6, affine=True)

    def forward(self, x):
        return self.gn(x)

class Swish(nn.Module):
    def forward(self, x):
        return x * torch.sigmoid(x)

class AxialBlock(nn.Module):
    def __init__(self, n_hiddens, n_head):
        super().__init__()
        self.attn_w = nn.MultiheadAttention(n_hiddens, n_head)
        self.attn_h = nn.MultiheadAttention(n_hiddens, n_head)
        self.attn_t = nn.MultiheadAttention(n_hiddens, n_head)

    def forward(self, x):
        x = shift_dim(x, 1, -1)
        x = self.attn_w(x, x, x)[0] + self.attn_h(x, x, x)[0] + self.attn_t(x, x, x)[0]
        x = shift_dim(x, -1, 1)
        return x

class AttnResidualBlock(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(ResidualBlock, self).__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.block = nn.Sequential(
            GroupNorm(in_channels),
            Swish(),
            nn.Conv3d(in_channels, out_channels, (3, 3, 3), (1, 1, 1), (1, 1, 1)),
            GroupNorm(out_channels),
            Swish(),
            nn.Conv3d(out_channels, out_channels, (3, 3, 3), (1, 1, 1), (1, 1, 1)),
            GroupNorm(out_channels),
            Swish()
            AxialBlock(out_channels, 2)
        )

        if out_channels != in_channels:
            self.channel_up = nn.Conv3d(in_channels, out_channels, (1, 1, 1), (1, 1, 1), (0, 0, 0))

    def forward(self, x):
        if self.in_channels != self.out_channels:
            return self.channel_up(x) + self.block(x)

        return x + self.block(x)
    
class UpSampleBlock(nn.Module):
    def __init__(self, channels):
        super(UpSampleBlock, self).__init__()
        self.conv = nn.Conv3d(channels, channels, (3, 3, 3), (1, 1, 1), (1, 1, 1))

    def forward(self, x):
        x = F.interpolate(x, scale_factor=2.)
        return self.conv(x)

class DownSampleBlock(nn.Module):
    def __init__(self, channels):
        super(DownSampleBlock, self).__init__()
        self.conv = nn.Conv3d(channels, channels, (3, 3, 3), (2, 2, 2), (0, 0, 0))

    def forward(self, x):
        pad = (0, 1, 0, 1)
        x = F.pad(x, pad, mode="constant", value=0)
        return self.conv(x)

class NonLocalBlock(nn.Module):
    def __init__(self, channels):
        super(NonLocalBlock, self).__init__()
        self.in_channels = channels

        self.gn = GroupNorm(channels)
        self.q = nn.Conv3d(channels, channels, (1, 1, 1), (1, 1, 1), (0, 0, 0))
        self.k = nn.Conv3d(channels, channels, (1, 1, 1), (1, 1, 1), (0, 0, 0))
        self.v = nn.Conv3d(channels, channels, (1, 1, 1), (1, 1, 1), (0, 0, 0))
        self.proj_out = nn.Conv3d(channels, channels, (1, 1, 1), (1, 1, 1), (0, 0, 0))

    def forward(self, x):
        h_ = self.gn(x)
        q = self.q(h_)
        k = self.k(h_)
        v = self.v(h_)

        b, c, h, w = q.shape

        q = q.reshape(b, c, h*w)
        q.permute(0, 2, 1)
        k = k.reshape(b, c, h*w)
        v = v.reshape(b, c, h*w)

        attn = torch.bmm(q, k)
        att *= int(c)**(-0.5)
        att = F.softmax(attn, dim=2)
        attn = attn.permute(0, 2, 1)

        A = torch.bmm(v, attn)
        A = A.reshape(b, c, h, w)

        return x + A