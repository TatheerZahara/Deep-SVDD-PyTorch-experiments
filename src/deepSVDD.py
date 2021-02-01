import json
import torch
import matplotlib.pyplot as plt

from base.base_dataset import BaseADDataset
from networks.main import build_network, build_autoencoder, build_decoder
from optim.deepSVDD_trainer import DeepSVDDTrainer
from optim.ae_trainer import AETrainer
from optim.de_trainer import DETrainer


class DeepSVDD(object):
    """A class for the Deep SVDD method.

    Attributes:
        objective: A string specifying the Deep SVDD objective (either 'one-class' or 'soft-boundary').
        nu: Deep SVDD hyperparameter nu (must be 0 < nu <= 1).
        R: Hypersphere radius R.
        c: Hypersphere center c.
        net_name: A string indicating the name of the neural network to use.
        net: The neural network \phi.
        ae_net: The autoencoder network corresponding to \phi for network weights pretraining.
        trainer: DeepSVDDTrainer to train a Deep SVDD model.
        optimizer_name: A string indicating the optimizer to use for training the Deep SVDD network.
        ae_trainer: AETrainer to train an autoencoder in pretraining.
        ae_optimizer_name: A string indicating the optimizer to use for pretraining the autoencoder.
        results: A dictionary to save the results.
    """

    def __init__(self, objective: str = 'one-class', nu: float = 0.1):
        """Inits DeepSVDD with one of the two objectives and hyperparameter nu."""

        assert objective in ('one-class', 'soft-boundary'), "Objective must be either 'one-class' or 'soft-boundary'."
        self.objective = objective
        assert (0 < nu) & (nu <= 1), "For hyperparameter nu, it must hold: 0 < nu <= 1."
        self.nu = nu
        self.R = 0.0  # hypersphere radius R
        self.c = None  # hypersphere center c

        self.net_name = None
        self.net = None  # neural network \phi

        self.trainer = None
        self.optimizer_name = None

        self.ae_net = None  # autoencoder network for pretraining
        self.ae_trainer = None
        self.ae_optimizer_name = None

        self.results = {
            'train_time': None,
            'test_auc': None,
            'test_time': None,
            'test_scores': None,
        }

    def set_network(self, net_name):
        """Builds the neural network \phi."""
        self.net_name = net_name
        self.net = build_network(net_name)

    def train(self, dataset: BaseADDataset, optimizer_name: str = 'adam', lr: float = 0.001, n_epochs: int = 50,
              lr_milestones: tuple = (), batch_size: int = 128, weight_decay: float = 1e-6, device: str = 'cuda',
              n_jobs_dataloader: int = 0):
        """Trains the Deep SVDD model on the training data."""

        self.optimizer_name = optimizer_name
        self.trainer = DeepSVDDTrainer(self.objective, self.R, self.c, self.nu, optimizer_name, lr=lr,
                                       n_epochs=n_epochs, lr_milestones=lr_milestones, batch_size=batch_size,
                                       weight_decay=weight_decay, device=device, n_jobs_dataloader=n_jobs_dataloader)
        # Get the model
        self.net = self.trainer.train(dataset, self.net)
        self.train_outputs = self.trainer.train_outputs
        self.R = float(self.trainer.R.cpu().data.numpy())  # get float
        self.c = self.trainer.c.cpu().data.numpy().tolist()  # get list
        self.results['train_time'] = self.trainer.train_time

    def test(self, dataset: BaseADDataset, device: str = 'cuda', n_jobs_dataloader: int = 0):
        """Tests the Deep SVDD model on the test data."""

        if self.trainer is None:
            self.trainer = DeepSVDDTrainer(self.objective, self.R, self.c, self.nu,
                                           device=device, n_jobs_dataloader=n_jobs_dataloader)

        self.trainer.test(dataset, self.net)
        # Get results
        self.test_outputs = self.trainer.test_outputs
        self.results['test_auc'] = self.trainer.test_auc
        self.results['test_time'] = self.trainer.test_time
        self.results['test_scores'] = self.trainer.test_scores

    def pretrain(self, dataset: BaseADDataset, optimizer_name: str = 'adam', lr: float = 0.001, n_epochs: int = 100,
                 lr_milestones: tuple = (), batch_size: int = 128, weight_decay: float = 1e-6, device: str = 'cuda',
                 n_jobs_dataloader: int = 0):
        """Pretrains the weights for the Deep SVDD network \phi via autoencoder."""

        self.ae_net = build_autoencoder(self.net_name)
        self.ae_optimizer_name = optimizer_name
        self.ae_trainer = AETrainer(optimizer_name, lr=lr, n_epochs=n_epochs, lr_milestones=lr_milestones,
                                    batch_size=batch_size, weight_decay=weight_decay, device=device,
                                    n_jobs_dataloader=n_jobs_dataloader)
        self.ae_net = self.ae_trainer.train(dataset, self.ae_net)
        self.ae_trainer.test(dataset, self.ae_net)
        self.init_network_weights_from_pretraining()
        self.loss_ae = self.ae_trainer.loss_ae
        self.testloss_ae = self.ae_trainer.testloss_ae

    def init_network_weights_from_pretraining(self):
        """Initialize the Deep SVDD network weights from the encoder weights of the pretraining autoencoder."""

        net_dict = self.net.state_dict()
        ae_net_dict = self.ae_net.state_dict()

        # Filter out decoder network keys
        ae_net_dict = {k: v for k, v in ae_net_dict.items() if k in net_dict}
        # Overwrite values in the existing state_dict
        net_dict.update(ae_net_dict)
        # Load the new state_dict
        self.net.load_state_dict(net_dict)
        
    def init_network_weights_for_posttraining(self):
        """Initialize the Deep SVDD network from the decoder weights of the pretraining autoencoder"""
        
        ae_net_dict = self.ae_net.state_dict()
        de_net_dict = self.de_net.state_dict()
        
        # Filter out decoder network keys
        ae_net_dict = {k: v for k, v in ae_net_dict.items() if k in de_net_dict}
        # Overwrite values in the existing state_dict
        de_net_dict.update(ae_net_dict)
        # Load the new state_dict
        self.de_net.load_state_dict(de_net_dict)
        
    def posttrain(self, dataset: BaseADDataset, optimizer_name: str = 'adam', lr: float = 0.001, n_epochs: int = 100,
                 lr_milestones: tuple = (), batch_size: int = 128, weight_decay: float = 1e-6, device: str = 'cuda',
                 n_jobs_dataloader: int = 0):
        """Post-trains the weights for the Deep SVDD network \phi via autoencoder."""

        self.de_net = build_decoder(self.net_name)
        #self.init_network_weights_for_posttraining()
        self.de_optimizer_name = optimizer_name
        self.de_trainer = DETrainer(optimizer_name, lr=lr, n_epochs=n_epochs, lr_milestones=lr_milestones,
                                    batch_size=batch_size, weight_decay=weight_decay, device=device,
                                    n_jobs_dataloader=n_jobs_dataloader)
        self.de_net = self.de_trainer.train(dataset, self.train_outputs, self.de_net)
        self.de_trainer.test(dataset, self.test_outputs, self.de_net)
        self.loss_de = self.de_trainer.loss_de
        self.testloss_de = self.de_trainer.testloss_de
        self.compare_AEDE()
        self.compare_train()
        self.compare_test()

    def save_model(self, export_model, save_ae=True):
        """Save Deep SVDD model to export_model."""

        net_dict = self.net.state_dict()
        ae_net_dict = self.ae_net.state_dict() if save_ae else None

        torch.save({'R': self.R,
                    'c': self.c,
                    'net_dict': net_dict,
                    'ae_net_dict': ae_net_dict}, export_model)

    def load_model(self, model_path, load_ae=False):
        """Load Deep SVDD model from model_path."""

        model_dict = torch.load(model_path)

        self.R = model_dict['R']
        self.c = model_dict['c']
        self.net.load_state_dict(model_dict['net_dict'])
        if load_ae:
            if self.ae_net is None:
                self.ae_net = build_autoencoder(self.net_name)
            self.ae_net.load_state_dict(model_dict['ae_net_dict'])

    def save_results(self, export_json):
        """Save results dict to a JSON-file."""
        with open(export_json, 'w') as fp:
            json.dump(self.results, fp)
    
    def compare_AEDE(self):
        x = range(1,151)
        plt.figure(figsize=(20,10))        
        plt.plot(x,self.loss_ae,color='red',label='Orig Autoenc')
        plt.plot(x,self.loss_de,color='blue',label='After SVDD Loss')
        plt.title('Training loss comparison')
        plt.xlabel('epochs')
        plt.ylabel('loss')
        plt.legend()
        plt.savefig('train_comp.png')
        
    def compare_train(self):
        plt.figure(figsize=(10,10))
        plt.bar(['Orig Autoenc','After SVDD Loss'],[self.loss_ae[-1],self.loss_de[-1]])
        plt.title('Final training loss comparison')
        plt.xlabel('categories')
        plt.ylabel('loss')
        plt.savefig('trainloss_comp.png')
        
    def compare_test(self):
        plt.figure(figsize=(10,10))
        plt.bar(['Orig Autoenc','After SVDD Loss'],[self.testloss_ae,self.testloss_de])
        plt.title('Test loss comparison')
        plt.xlabel('categories')
        plt.ylabel('loss')
        plt.savefig('testloss_comp.png')
        