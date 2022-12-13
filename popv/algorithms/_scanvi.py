import scanpy as sc
import numpy as np
import logging
import scvi

from sklearn.neighbors import KNeighborsClassifier
from typing import Optional, Literal


class SCANVI_POPV:
    def __init__(
        self,
        batch_key: Optional[str] = "_batch_annotation",
        labels_key: Optional[str] = "_labels_annotation",
        n_epochs_unsupervised: Optional[int] = None,
        n_epochs_semisupervised: Optional[int] = 20,
        use_gpu: Optional[bool] = False,
        save_folder: Optional[str] = None,
        result_key: Optional[str] = "popv_scanvi_prediction",
        embedding_key: Optional[str] = "X_scanvi_umap_popv",
        model_kwargs: Optional[dict] = {},
        classifier_kwargs: Optional[dict] = {},
        embedding_dict: Optional[dict] = {},
        ) -> None:
        """
        Class to compute classifier in scANVI model and predict labels.

        Parameters
        ----------
        batch_key
            Key in obs field of adata for batch information.
        labels_key
            Key in obs field of adata for cell-type information.
        n_epochs_unsupervised
            Number of epochs scvi is trained in unsupervised mode.
        n_epochs_semisupervised
            Number of epochs scvi is trained in semisupervised mode.
        use_gpu
            Whether gpu is used for training.
        result_key
            Key in obs in which celltype annotation results are stored.
        embedding_key
            Key in obsm in which UMAP embedding of integrated data is stored.
        model_kwargs
            Dictionary to supply non-default values for SCVI model. Options at scvi.model.SCVI
        classifier_kwargs
            Dictionary to supply non-default values for SCANVI classifier.
            Options at classifier_paramerers in scvi.model.SCANVI.from_scvi_model.
        embedding_dict
            Dictionary to supply non-default values for UMAP embedding. Options at sc.tl.umap
        """

        self.batch_key = batch_key
        self.labels_key = labels_key
        self.result_key = result_key
        self.embedding_key = embedding_key

        self.n_epochs_unsupervised = n_epochs_unsupervised
        self.n_epochs_semisupervised = n_epochs_semisupervised
        self.use_gpu = use_gpu
        self.save_folder = save_folder

        self.model_kwargs = {
            "dropout_rate": 0.1,
            "dispersion": "gene-batch",
            "n_layers": 2,
            "n_latent": 20,
        }
        self.model_kwargs.update(model_kwargs)

        self.classifier_kwargs = {
            "n_layers": 1,
            "dropout_rate": 0.2
        }
        self.classifier_kwargs.update(classifier_kwargs)

        self.embedding_dict = {
            "min_dist": 0.01
        }
        self.embedding_dict.update(embedding_dict)

    def compute_integration(self, adata):
        logging.info("Integrating data with scANVI")

        # Go through obs field with subsampling information and subsample label information.
        adata.obs["subsampled_labels"] = [
            label if subsampled else adata.uns['unknown_celltype_label'] for label, subsampled in
            zip(adata.obs['_labels_annotation'], adata.obs['_ref_subsample'])]

        pretrained_scanvi_path = adata.uns["_pretrained_scanvi_path"]

        if pretrained_scanvi_path is None:
            scvi.model.SCVI.setup_anndata(
                adata,
                batch_key=self.batch_key,
                labels_key="subsampled_labels",
                layer="scvi_counts")
            scvi_model = scvi.model.SCVI(
                adata,
                **self.model_kwargs)
            scvi_model.train(
                train_size=1.0,
                max_epochs=self.n_epochs_unsupervised,
                use_gpu=adata.uns["_use_gpu"])

            self.model = scvi.model.SCANVI.from_scvi_model(
                scvi_model,
                unlabeled_category=adata.uns['unknown_celltype_label'],
                classifier_parameters=self.classifier_kwargs)
        else:
            query = adata[adata.obs["_dataset"] == "query"].copy()
            self.model = scvi.model.SCANVI.load_query_data(
                query,
                pretrained_scanvi_path,
                freeze_classifier=True
            )

        if self.n_epochs_unsupervised is None:
            self.n_epochs_unsupervised = np.min([round((20000 / adata.n_obs) * 200), 200])

        self.model.train(
            max_epochs=self.n_epochs_semisupervised,
            train_size=1.0,
            use_gpu=adata.uns["_use_gpu"]
        )

        adata.obsm['X_scanvi'] = self.model.get_latent_representation(adata)

        if self.save_folder is not None:
            self.model.save(self.save_folder, overwrite=True, save_anndata=False)

    def predict(self, adata):
        logging.info('Saving scanvi label prediction to adata.obs["{}"]'.format(self.result_key))

        adata.obs[self.result_key] = self.model.predict(adata)

    def compute_embedding(self, adata):
        logging.info('Saving UMAP of scanvi results to adata.obs["{}"]'.format(self.embedding_key))

        sc.pp.neighbors(adata, use_rep="X_scanvi")
        adata.obsm[self.embedding_key] = sc.tl.umap(adata, copy=True, **self.embedding_dict).obsm['X_umap']
