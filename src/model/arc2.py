import torch
import torch.nn as nn
import torch.nn.functional as F

from model.sparse_linear import SparseLinear, pairwise


class MatchMatrix(nn.Module):

    def __init__(
            self,
            input_size,
            matrix_depth,
            dropout
    ):
        super(MatchMatrix, self).__init__()
        self.dropout = dropout
        activation = nn.LeakyReLU

        self.matrix_depth = matrix_depth

        interaction = [
            nn.Linear(input_size * 2, matrix_depth[0]),
            activation()
        ]

        for from_size, to_size in pairwise(self.matrix_depth):
            interaction += [
                nn.Linear(from_size, to_size),
                activation(),
                nn.Dropout(self.dropout)
            ]

        self.interaction = nn.Sequential(*interaction)

    def forward(self, sent_a, sent_b):
        a_size = sent_a.shape[1]
        b_size = sent_b.shape[1]

        a = torch.stack([sent_a] * b_size, dim=2)
        b = torch.stack([sent_b] * a_size, dim=1)

        matrix = torch.cat([a, b], dim=3)

        return self.interaction(matrix)

    def weight_init(self, init_foo):

        def init_weights(m):
            if isinstance(m, (nn.Linear, nn.Conv2d)):
                init_foo(m.weight)
                # nn.init.xavier_uniform_(m.bias, gain=nn.init.calculate_gain('tanh'))

        self.interaction.apply(init_weights)


class SumVectorizer(nn.Module):
    def __init__(
            self,
            word_emb_sizes,
            embedding_size
    ):
        super(SumVectorizer, self).__init__()

        self.embedding_size = embedding_size
        self.word_emb_sizes = word_emb_sizes

        self.embedding = SparseLinear(dict_size=self.embedding_size, out_features=self.word_emb_sizes)

    def forward(self, sent_a):
        return self.embedding(sent_a)

    def weight_init(self, init_foo):

        def init_weights(m):
            if isinstance(m, (nn.Linear, nn.Conv2d)):
                init_foo(m.weight)

        self.embedding.apply(init_weights)


class PreConv(nn.Module):
    def __init__(
            self,
            word_emb_sizes,
            sent_conv_size,
            dropout,
            window
    ):
        super(PreConv, self).__init__()

        self.window = window
        self.dropout = dropout
        self.sent_conv_size = sent_conv_size
        self.word_emb_sizes = word_emb_sizes
        activation = nn.LeakyReLU

        input_to_word_vect = []
        self.input_to_vect = None

        for from_size, to_size in pairwise(self.word_emb_sizes):
            input_to_word_vect += [
                nn.Linear(from_size, to_size),
                activation(),
                nn.Dropout(self.dropout),
            ]

        if len(input_to_word_vect) > 0:
            self.input_to_vect = nn.Sequential(*input_to_word_vect)

        self.sent_conv = None

        if self.sent_conv_size and len(self.sent_conv_size) > 0:
            self.sent_conv = nn.Sequential(*[
                torch.nn.Conv1d(self.word_emb_sizes[-1], self.sent_conv_size[0], self.window),
                activation(),
                nn.Dropout(0.2),
            ])

            self.output_size = self.sent_conv_size[-1]
        else:
            self.output_size = self.word_emb_sizes[-1]

    def weight_init(self, init_foo):

        def init_weights(m):
            if isinstance(m, (nn.Linear, nn.Conv2d)):
                init_foo(m.weight)
                # nn.init.xavier_uniform_(m.bias, gain=nn.init.calculate_gain('tanh'))
        if self.input_to_vect:
            self.input_to_vect.apply(init_weights)

    def forward(self, sent_embedding_a):

        if self.input_to_vect:
            sent_embedding_a = self.input_to_vect(sent_embedding_a)

        if self.sent_conv:
            sent_embedding_a = sent_embedding_a.transpose(1, 2)
            sent_embedding_a = self.sent_conv(sent_embedding_a).transpose(2, 1)

        return sent_embedding_a


class ARC2(nn.Module):

    def __init__(
            self,
            vectorizer,
            preconv,
            matrix_depth,
            conv_depth,
            out_size,
            window,
            dropout=0.1
    ):
        super(ARC2, self).__init__()
        self.vectorizer = vectorizer
        self.preconv = preconv

        self.window = window
        self.out_size = out_size
        self.conv_depth = conv_depth
        self.matrix_depth = matrix_depth

        self.dropout = dropout
        activation = nn.LeakyReLU

        self.match_layer = MatchMatrix(preconv.output_size, self.matrix_depth, dropout=self.dropout)

        convolutions = [
            torch.nn.Conv2d(self.matrix_depth[-1], self.conv_depth[0], self.window),
            activation(),
        ]

        for from_size, to_size in pairwise(self.conv_depth):
            convolutions += [
                torch.nn.MaxPool2d(2, padding=1),
                torch.nn.Conv2d(from_size, to_size, self.window),
                activation(),
            ]

        self.convolution = nn.Sequential(*convolutions)

        feed_forward = [
            nn.Linear(in_features=self.conv_depth[-1], out_features=self.out_size[0])
        ]

        for from_size, to_size in pairwise(self.out_size):
            feed_forward += [
                activation(),
                nn.Dropout(self.dropout),
                torch.nn.Linear(from_size, to_size)
            ]

        feed_forward.append(nn.Linear(in_features=self.out_size[-1], out_features=2))
        feed_forward.append(nn.Softmax(dim=1))

        self.feed_forward = nn.Sequential(*feed_forward)

    def weight_init(self, init_foo):

        self.match_layer.weight_init(init_foo)

        if self.vectorizer:
            self.vectorizer.weight_init(init_foo)

        if self.preconv:
            self.preconv.weight_init(init_foo)

        def init_weights(m):
            if isinstance(m, (nn.Linear, nn.Conv2d)):
                init_foo(m.weight)
                # nn.init.xavier_uniform_(m.bias, gain=nn.init.calculate_gain('tanh'))

        self.convolution.apply(init_weights)
        self.feed_forward.apply(init_weights)

    def forward(self, sent_a, sent_b):

        if self.vectorizer:
            sent_a = self.vectorizer(sent_a)
            sent_b = self.vectorizer(sent_b)

        if self.preconv:
            sent_a = self.preconv(sent_a)
            sent_b = self.preconv(sent_b)

        match_matrix = self.match_layer(sent_a, sent_b)

        match_matrix = match_matrix.transpose(3, 1)  # Output: (N, Channels, Height, Width)

        conv_matrix = self.convolution(match_matrix)

        _, _, h, w = conv_matrix.shape

        max_pooling = F.max_pool2d(conv_matrix, kernel_size=(h, w)).view(-1, self.conv_depth[-1])

        res = self.feed_forward(max_pooling)

        return res
