python train.py --c config/usb_nlp/fixmatch/fixmatch_ag_news_40_0.yaml --use_tensorboard --seed 1 --save_name fixmatch-e10_i10000_s1 --algorithm fixmatch --epoch 10 --num_train_iter 10000\
  -nl 4000 -bsz 32 --use_post_hoc_calib False  --n_cal 1000  --n_th 1000 --take_d_cal_th_from train_lb -ds ag_news