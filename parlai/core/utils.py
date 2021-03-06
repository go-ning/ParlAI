# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

from collections import deque
import math
import random
import time


class Predictor(object):
    """Provides functionality for setting up a running version of a model and
    requesting predictions from that model on live data.

    Note that this maintains no World state (does not use a World), merely
    providing the observation directly to the model and getting a response.

    This is limiting when it comes to certain use cases, but is
    """

    def __init__(self, args=None, **kwargs):
        """Initializes the predictor, setting up opt automatically if necessary.

        Args is expected to be in the same format as sys.argv: e.g. a list in
        the form ['--model', 'seq2seq', '-hs', 128, '-lr', 0.5].

        kwargs is interpreted by appending '--' to it and replacing underscores
        with hyphens, so 'dict_file=/tmp/dict.tsv' would be interpreted as
        '--dict-file /tmp/dict.tsv'.
        """
        from parlai.core.params import ParlaiParser
        from parlai.core.agents import create_agent

        if args is None:
            args = []
        for k, v in kwargs.items():
            args.append('--' + str(k).replace('_', '-'))
            args.append(str(v))
        parser = ParlaiParser(True, True, model_argv=args)
        self.opt = parser.parse_args(args)
        self.agent = create_agent(self.opt)

    def predict(self, observation):
        """From a ParlAI-standard observation dict, returns a prediction from
        the model.
        """
        if 'episode_done' not in observation:
            observation['episode_done'] = True
        self.agent.observe(observation)
        reply = self.agent.act()
        return reply


class Timer(object):
    """Computes elapsed time."""
    def __init__(self):
        self.running = True
        self.total = 0
        self.start = time.time()

    def reset(self):
        self.running = True
        self.total = 0
        self.start = time.time()
        return self

    def resume(self):
        if not self.running:
            self.running = True
            self.start = time.time()
        return self

    def stop(self):
        if self.running:
            self.running = False
            self.total += time.time() - self.start
        return self

    def time(self):
        if self.running:
            return self.total + time.time() - self.start
        return self.total


def round_sigfigs(x, sigfigs=4):
    try:
        if x == 0:
            return 0
    except RuntimeError:
        # handle 1D torch tensors
        x = x[0]
    if x in [float('inf'), float('-inf'), float('NaN')]:
        return x
    return round(x, -math.floor(math.log10(abs(x)) - sigfigs + 1))


def flatten(teacher, context_length=-1, include_labels=True):
    """Return a flattened version of a teacher's data where all episodes only
    have length one but contain the desired amount of context.

    If context_length is not None, will use only that many past utterances.
    Default is None. Setting it to one only uses the input text.

    If include_labels is True, will include a random label in past utterances.
    Default is True.
    """
    data = []
    current = []
    episode_done = False
    context_length = context_length if context_length >= 0 else None
    context = deque(maxlen=context_length)
    try:
        while not teacher.epoch_done():
            # collect examples in episode
            while not episode_done:
                action = teacher.act()
                current.append(action)
                episode_done = action['episode_done']

            # build separate episodes from each example
            for ex in current:
                context.append(ex.get('text', ''))
                if len(context) > 1:
                    ex['text'] = '\n'.join(context)
                ex['episode_done'] = True
                if include_labels:
                    # add labels to context
                    labels = ex.get('labels', ex.get('eval_labels'))
                    if labels is not None:
                        context.append(random.choice(labels))
                data.append(ex)
            # reset flags and content
            episode_done = False
            current.clear()
            context.clear()
        return data
    except MemoryError as ex:
        raise MemoryError('Ran out of memory building flattened data batches. '
                          'Try using --context-length set to a small value to '
                          'limit the length of each flattened example, '
                          'disabling batch sorting / flattening by setting '
                          '--batch-sort false, or switching to data streaming '
                          'using --datatype {type}:stream to read from disk '
                          'if it is supported for your dataset.')


def sort_data(data, key='text_label', method='spaces'):
    """Given a list of data, sort it according to the method and key.

    Currently the only supported method is counting the number of spaces.
    This appeared to be reliable enough and much faster than tokenizing.
    It performs much better than just using the length of the string.

    Currently the only supported key is sorting by first the text, then the
    label.
    See https://arxiv.org/abs/1706.05765 for an evaulation of alternative
    approaches for machine translation.
    Sorting by the source (text) gives a good improvement in speed over random
    batching and is robust to different types of optimization.
    Breaking ties by sorting by label length gives a further improvement in
    speed but can reduce robustness with some optimization schemes.
    """
    # TODO: support different keys and different methods
    tpls = []
    for ex in data:
        # first sort by input length
        fst = ex.get('text', '').count(' ')

        # then sort by target length (don't sort by eval_labels, no need)
        snd = 0
        labels = ex.get('labels', None)
        if labels is not None:
            # use average label length (probably just one answer usually)
            snd = sum(l.count(' ') for l in labels) / len(labels)

        tiebreaker = random.random()
        tpls.append((fst, snd, tiebreaker, ex))
    tpls.sort()
    return [e[-1] for e in tpls]


def make_batches(data, bsz):
    """Return a list of lists of size bsz given a list of examples."""
    return [data[i:i + bsz] for i in range(0, len(data), bsz)]
