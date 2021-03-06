import argparse
import json
import os
import sys

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import DataLoader
from tqdm import tqdm

from fakenews_detection.topic_feature import TopicFeature
from model import DetectModel
from utils.data import SeqDataset, collate_fn


def load_model(args, config):
    path = os.path.dirname(os.path.abspath(__file__))

    # Load pretrained topic feature
    topic_feature = TopicFeature(labels=(1, 0))
    topic_feature.load(args.topic_feature)

    data = [json.loads(d) for d in open(args.input, "rt").readlines()]
    dataset = SeqDataset(data, topic_feature)

    device = torch.device("cuda:{}".format(args.cuda) if args.cuda else "cpu")

    model = DetectModel(input_size=config['model']['input_size'], hidden_size=config['model']['hidden_size'],
                        rnn_layers=config['model']['rnn_layers'],
                        out_channels=config['model']['out_channels'], height=config['model']['height'],
                        cnn_layers=config['model']['cnn_layers'],
                        topic_size=config['model']['topic_size'],
                        linear_hidden_size=config['model']['linear_hidden_size'],
                        linear_layers=config['model']['linear_layers'], output_size=config['model']['output_size'])

    if args.test:
        checkpoint = torch.load(os.path.join(args.logdir, args.test))
        model.load_state_dict(checkpoint['state_dict'])
        if args.logdir is None:
            print("[-] No log directory option")
            sys.exit(1)

    model = model.to(device)

    dataloader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, drop_last=True, collate_fn=collate_fn)

    return model, dataset, dataloader, device


def predict(model: DetectModel, dataset: SeqDataset, dataloader: DataLoader, config: dict, device):
    h0 = torch.zeros(config['model']['rnn_layers'], dataloader.batch_size, config['model']['hidden_size']).to(device)

    true_acc = []
    false_acc = []

    with tqdm(total=len(dataset), desc="Sequences", leave=False) as pbar:
        for step, (sequences, tweets, labels) in enumerate(dataloader):
            pbar.update(args.batch_size)

            sequences = torch.tensor(sequences, dtype=torch.float, requires_grad=False).to(device)
            tweets = torch.tensor(tweets, dtype=torch.float, requires_grad=False).to(device)
            labels = torch.tensor(labels, dtype=torch.long, requires_grad=False).to(device)

            output = model(sequences, tweets, h0)
            output = F.softmax(output, dim=1).argmax(dim=1)

            for o, t in zip(output.tolist(), labels.tolist()):
                if t == 1:
                    true_acc.append(o)
                elif t == 0:
                    if o == 1:
                        false_acc.append(0)
                    elif o == 0:
                        false_acc.append(1)
    print("True acc: {}/{} ({:,})".format(sum(true_acc), len(true_acc), sum(true_acc) / len(true_acc)))
    print("False acc: {}/{} ({:,})".format(sum(false_acc), len(false_acc), sum(false_acc) / len(false_acc)))
    print("Total acc: {}/{} ({:,})".format(sum(true_acc + false_acc), (len(true_acc) + len(false_acc)), sum(true_acc + false_acc) / (len(true_acc) + len(false_acc))))


def train(model: DetectModel, dataset: SeqDataset, dataloader: DataLoader, config: dict, device):
    optimizer = optim.Adam(params=model.parameters(), lr=args.learning_rate)
    criterion = nn.CrossEntropyLoss()

    h0 = torch.zeros(config['model']['rnn_layers'], dataloader.batch_size, config['model']['hidden_size']).to(device)

    for epoch in tqdm(range(args.epoch), desc="Epochs"):
        with tqdm(total=len(dataset), desc="Sequences", leave=False) as pbar:
            for step, (sequences, tweets, labels) in enumerate(dataloader):
                pbar.update(args.batch_size)

                model.zero_grad()
                h0.data.zero_()

                sequences = torch.tensor(sequences, dtype=torch.float, requires_grad=False).to(device)
                tweets = torch.tensor(tweets, dtype=torch.float, requires_grad=False).to(device)
                labels = torch.tensor(labels, dtype=torch.long, requires_grad=False).to(device)

                output = model(sequences, tweets, h0)

                loss = criterion(output, labels)
                loss.backward()

                norm = nn.utils.clip_grad_norm_(model.parameters(), max_norm=5)
                optimizer.step()

                if step % 30 == 0:
                    tqdm.write("Step: {:,} Loss: {:.5f} L2 gradient norm: {:.5f}".format(step, loss, norm))

            if args.logdir is not None:
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.logdir)
                if not os.path.exists(path):
                    os.makedirs(path)

                state = {
                    'epoch': epoch,
                    'state_dict': model.state_dict(),
                    'optimizer': optimizer.state_dict()
                }
                torch.save(state, os.path.join(path, "{}.ckpt".format(epoch+1)))
                tqdm.write("[+] {}.ckpt saved".format(epoch))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, help="config file path (required)", required=True)
    parser.add_argument('--cuda', type=int, default=None, help="GPU number (default: None=CPU)")
    parser.add_argument('--logdir', type=str, help="log directory (optional)")
    parser.add_argument('--test', type=str, default=None, help="checkpoint path for test (optional)")

    parser.add_argument('--input', type=str, help="input file path (required)", required=True)
    parser.add_argument('--topic-feature', type=str, help="pretrained topic feature directory (required)", required=True)
    parser.add_argument('--learning-rate', type=float, default=0.2, metavar="0.2", help="learning rate for model")
    parser.add_argument('--batch-size', type=int, default=32, metavar='32', help="batch size for learning")
    parser.add_argument('--epoch', type=int, default=10, metavar="10", help="the number of epochs")

    args = parser.parse_args()
    config_json = json.load(open(args.config, "rt"))
    model, dataset, dataloader, device = load_model(args, config_json)

    if args.test:
        if args.logdir is None:
            print("[-] No log directory option")
            sys.exit(1)

        predict(model, dataset, dataloader, config_json, device)
    else:
        train(model, dataloader, dataloader, config_json, device)
