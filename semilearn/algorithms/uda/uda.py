# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import torch
import math
from semilearn.core import AlgorithmBase
from semilearn.algorithms.hooks import PseudoLabelingHook, FixedThresholdingHook
from semilearn.algorithms.utils import ce_loss, consistency_loss, SSL_Argument


class UDA(AlgorithmBase):
    """
    UDA algorithm (https://arxiv.org/abs/1904.12848).

    Args:
        - args (`argparse`):
            algorithm arguments
        - net_builder (`callable`):
            network loading function
        - tb_log (`TBLog`):
            tensorboard logger
        - logger (`logging.Logger`):
            logger to use
        - T (`float`):
            Temperature for pseudo-label sharpening
        - p_cutoff(`float`):
            Confidence threshold for generating pseudo-labels
        - hard_label (`bool`, *optional*, default to `False`):
            If True, targets have [Batch size] shape with int values. If False, the target is vector
        - tsa_schedule ('str'):
            TSA schedule to use
    """
    def __init__(self, args, net_builder, tb_log=None, logger=None):
        super().__init__(args, net_builder, tb_log, logger)
        # uda specificed arguments
        self.init(T=args.T, p_cutoff=args.p_cutoff, tsa_schedule=args.tsa_schedule)

    def init(self, T, p_cutoff, tsa_schedule='none'):
        self.T = T
        self.p_cutoff = p_cutoff
        self.tsa_schedule = tsa_schedule

    def set_hooks(self):
        self.register_hook(PseudoLabelingHook(), "PseudoLabelingHook")
        self.register_hook(FixedThresholdingHook(), "MaskingHook")
        super().set_hooks()

    def train_step(self, x_lb, y_lb, x_ulb_w, x_ulb_s):
        num_lb = y_lb.shape[0]

        # inference and calculate sup/unsup losses
        with self.amp_cm():
            if self.use_cat:
                inputs = torch.cat((x_lb, x_ulb_w, x_ulb_s))
                logits = self.model(inputs)
                logits_x_lb = logits[:num_lb]
                logits_x_ulb_w, logits_x_ulb_s = logits[num_lb:].chunk(2)
            else:
                logits_x_lb = self.model(x_lb)
                logits_x_ulb_s = self.model(x_ulb_s)
                with torch.no_grad():
                    logits_x_ulb_w = self.model(x_ulb_w)

            tsa = self.TSA(self.tsa_schedule, self.it, self.num_train_iter, self.num_classes)  # Training Signal Annealing
            sup_mask = torch.max(torch.softmax(logits_x_lb, dim=-1), dim=-1)[0].le(tsa).float().detach()
            sup_loss = (ce_loss(logits_x_lb, y_lb, reduction='none') * sup_mask).mean()

            # compute mask
            mask = self.call_hook("masking", "MaskingHook", logits_x_ulb=logits_x_ulb_w)

            # generate unlabeled targets using pseudo label hook
            pseudo_label = self.call_hook("gen_ulb_targets", "PseudoLabelingHook", 
                                          logits=logits_x_ulb_w,
                                          use_hard_label=False,
                                          T=self.T)

            unsup_loss = consistency_loss(logits_x_ulb_s,
                                          pseudo_label,
                                          'ce',
                                          mask=mask)

            total_loss = sup_loss + self.lambda_u * unsup_loss

        # parameter updates
        self.call_hook("param_update", "ParamUpdateHook", loss=total_loss)

        tb_dict = {}
        tb_dict['train/sup_loss'] = sup_loss.item()
        tb_dict['train/unsup_loss'] = unsup_loss.item()
        tb_dict['train/total_loss'] = total_loss.item()
        tb_dict['train/mask_ratio'] = mask.float().mean().item()

        return tb_dict

    def TSA(self, schedule, cur_iter, total_iter, num_classes):
        training_progress = cur_iter / total_iter

        if schedule == 'linear':
            threshold = training_progress
        elif schedule == 'exp':
            scale = 5
            threshold = math.exp((training_progress - 1) * scale)
        elif schedule == 'log':
            scale = 5
            threshold = 1 - math.exp((-training_progress) * scale)
        elif schedule == 'none':
            return 1
        tsa = threshold * (1 - 1 / num_classes) + 1 / num_classes
        return tsa

    @staticmethod
    def get_argument():
        return [
            SSL_Argument('--tsa_schedule', str, 'none', 'TSA mode: none, linear, log, exp'),
            SSL_Argument('--T', float, 0.4, 'Temperature sharpening'),
            SSL_Argument('--p_cutoff', float, 0.8, 'confidencial masking threshold'),
            # SSL_Argument('--use_flex', str2bool, False),
        ]
