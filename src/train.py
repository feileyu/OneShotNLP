"""
This script should perform training of the CDSSM model
"""
import nltk
import torch
from torch.autograd import Variable
import torch.optim as optim
from tqdm import tqdm

from model.siames import Siames
from utils.loader import MentionsLoader
from utils.text_tools import encode_texts, pad_batch

tqdm.monitor_interval = 0


def tokenizer(text, alpha_only=True):  # create a tokenizer function
    return [tok for tok in nltk.word_tokenize(text) if (not alpha_only or tok.isalpha())]


use_cuda = False

loader = MentionsLoader(MentionsLoader.test_data)
model = Siames()
if use_cuda:
    model.cuda()


epoch_max = 10
batch_size = 500
batch_sample_size = 10
dict_size = 20000

optimizer = optim.Adam(model.parameters(), lr=1e-4)


def loss_foo(distances, target, alpha=0.4):
    """

    :param alpha:
    :param distances: 1d Tensor shape: (num_examples, )
    :param target: 1d Tensor shape: (num_examples, )
    """

    diff = torch.abs(distances - target)
    return torch.sum(diff[diff > alpha])


pbar = tqdm(range(epoch_max))
for epoch in pbar:
    for idx, batch in enumerate(loader.read_batches(batch_size)):
        sentences_a, sentences_b, match = loader.random_batch_constructor(batch, batch_sample_size)

        batch_a = Variable(torch.from_numpy(pad_batch(encode_texts(sentences_a, dict_size, tokenizer=tokenizer))))
        batch_b = Variable(torch.from_numpy(pad_batch(encode_texts(sentences_b, dict_size, tokenizer=tokenizer))))
        target = Variable(torch.FloatTensor(match))

        if use_cuda:
            batch_a = batch_a.cuda()
            batch_b = batch_b.cuda()
            target = target.cuda()

        distances = model(batch_a, batch_b)

        loss = loss_foo(distances, target)

        pbar.set_description("Loss {0:.1f}".format(loss))

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()


