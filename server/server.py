
#! Noted that
#! iteration in frontend is start+(iteration -1)*period

# TODO change to timevis format
# TODO set a base class for some trainer functions... we dont need too many hyperparameters for frontend

from PIL import Image
import matplotlib.pyplot as plt

from unicodedata import name
from flask import request, Response, Flask, jsonify, make_response
from flask_cors import CORS, cross_origin
import base64
import os
import sys
import json
import torch
import pandas as pd
import numpy as np

# import tensorflow as tf
# from umap.umap_ import find_ab_params
from sqlalchemy import create_engine, text

from antlr4 import *
# import MyGrammarLexer
# import MyGrammarParser
# import MyGrammarPrintListener

sys.path.append("..")
from singleVis.SingleVisualizationModel import SingleVisualizationModel
from singleVis.data import DataProvider
from singleVis.eval.evaluator import Evaluator
from singleVis.trainer import SingleVisTrainer
from singleVis.losses import ReconstructionLoss, UmapLoss, SingleVisLoss
from singleVis.visualizer import visualizer
from BackendAdapter import TimeVisBackend


# flask for API server
app = Flask(__name__)
cors = CORS(app, supports_credentials=True)
app.config['CORS_HEADERS'] = 'Content-Type'


@app.route('/load', methods=["POST", "GET"])
@cross_origin()
def load():
    res = request.get_json()
    CONTENT_PATH = os.path.normpath(res['path'])
    sys.path.append(CONTENT_PATH)

    # load hyperparameters
    config_file = os.path.join(CONTENT_PATH, "config.json")
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except:
        raise NameError("config file not exists...")

    CLASSES = config["CLASSES"]
    DATASET = config["DATASET"]
    LAMBDA = config["TRAINING"]["LAMBDA"]
    EPOCH_START = config["EPOCH_START"]
    EPOCH_END = config["EPOCH_END"]
    EPOCH_PERIOD = config["EPOCH_PERIOD"]
    SUBJECT_MODEL_NAME = config["TRAINING"]["SUBJECT_MODEL_NAME"]
    VIS_MODEL_NAME = config["VISUALIZATION"]["VIS_MODEL_NAME"]
    RESOLUTION = config["VISUALIZATION"]["RESOLUTION"]


    # define hyperparameters
    DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    import Model.model as subject_model
    try:
        net = eval("subject_model.{}()".format(SUBJECT_MODEL_NAME))
    except:
        raise NameError("No subject model found in model.py...")

    data_provider = DataProvider(CONTENT_PATH, net, EPOCH_START, EPOCH_END, EPOCH_PERIOD, split=-1, device=DEVICE, verbose=1)
    model = SingleVisualizationModel(input_dims=512, output_dims=2, units=256)
    negative_sample_rate = 5
    min_dist = .1
    # _a, _b = find_ab_params(1.0, min_dist)
    umap_loss_fn = UmapLoss(negative_sample_rate, DEVICE, _a=[1.0], _b=[1.0], repulsion_strength=1.0)
    recon_loss_fn = ReconstructionLoss(beta=1.0)
    criterion = SingleVisLoss(umap_loss_fn, recon_loss_fn, lambd=LAMBDA)

    optimizer = torch.optim.Adam(model.parameters(), lr=.01, weight_decay=1e-5)
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=4, gamma=.1)

    trainer = SingleVisTrainer(model, criterion=criterion, optimizer=optimizer, lr_scheduler=lr_scheduler, edge_loader=None, DEVICE=DEVICE)
    trainer.load(file_path=os.path.join(data_provider.model_path,"{}".format(VIS_MODEL_NAME)))
    trainer.model.eval()

    # vis = visualizer(data_provider, trainer.model, RESOLUTION, 10, CLASSES)
    evaluator = Evaluator(data_provider, trainer)
    timevis = TimeVisBackend(data_provider, trainer, evaluator)


    sql_engine       = create_engine('mysql+pymysql://xg:password@localhost/dviDB', pool_recycle=3600)
    db_connection    = sql_engine.connect()

    # Search the following tables in MYSQL database and drop them if they exist
    sql_engine.execute(text('DROP TABLE IF EXISTS SubjectModel;'))
    sql_engine.execute(text('DROP TABLE IF EXISTS VisModel;'))
    sql_engine.execute(text('DROP TABLE IF EXISTS Sample;'))
    sql_engine.execute(text('DROP TABLE IF EXISTS NoisySample;'))
    sql_engine.execute(text('DROP TABLE IF EXISTS AlSample;'))
    sql_engine.execute(text('DROP TABLE IF EXISTS PredSample;'))

    # Create the SubjectModel table in MYSQL database and insert the data
    table_subject_model = "SubjectModel"
    data_subject_model = timevis.subject_model_table()
    data_subject_model.to_sql(table_subject_model, db_connection, if_exists='fail');

    # Create the VisModel table in MYSQL database and insert the data
    table_vis_model = "VisModel"
    data_vis_model = timevis.vis_model_table()
    data_vis_model.to_sql(table_vis_model, db_connection, if_exists='fail');

    # Create the Sample table in MYSQL database and insert the data
    table_sample = "Sample"
    data_sample = timevis.sample_table()
    data_sample.to_sql(table_sample, db_connection, if_exists='fail');

    # For nosiy or active learning data, currently not tested yet
    if "noisy" in CONTENT_PATH:     
        table_noisy_sample = "NoisySample"
        data_noisy_sample = timevis.sample_table_noisy()
        data_noisy_sample.to_sql(table_noisy_sample, db_connection, if_exists='fail');
    elif "active" in CONTENT_PATH:
        table_al_sample = "AlSample"
        data_al_sample = timevis.sample_table_AL()
        data_al_sample.to_sql(table_al_sample, db_connection, if_exists='fail');
    
    # Ablation starts here
    # Store prediction, deltaboundary true/false for all samples in all epochs in PredSample table
    all_prediction_list = []
    all_deltab_list = []
    all_epochs_list = []
    all_idx_list = []
    for iteration in range(data_provider.s, data_provider.e+1, data_provider.p):
        print("iteration", iteration)
        train_data = data_provider.train_representation(iteration)
        test_data = data_provider.test_representation(iteration)
        all_data = np.concatenate((train_data, test_data), axis=0)

        prediction = data_provider.get_pred(iteration, all_data).argmax(-1)
        deltab = data_provider.is_deltaB(iteration, all_data)

        count = 0
        for idx,_ in enumerate(prediction):
            all_prediction_list.append(prediction[idx])
            all_deltab_list.append(deltab[idx])
            all_epochs_list.append(iteration)
            all_idx_list.append(count)
            count += 1

    data_pred_sample = pd.DataFrame(list(zip(all_idx_list, all_epochs_list, all_prediction_list, all_deltab_list)),
               columns =['idx', 'epoch', 'pred', 'deltab'])
    table_pred_sample = "PredSample"
    data_pred_sample.to_sql(table_pred_sample, db_connection, if_exists='fail')

    db_connection.close()

    # TODO have not finish loading all the points


@app.route('/updateProjection', methods=["POST", "GET"])
@cross_origin()
def update_projection():
    res = request.get_json()
    CONTENT_PATH = os.path.normpath(res['path'])
    iteration = int(res['iteration'])
    predicates = res["predicates"]
    sys.path.append(CONTENT_PATH)

    # load hyperparameters
    config_file = os.path.join(CONTENT_PATH, "config.json")
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except:
        raise NameError("config file not exists...")

    CLASSES = config["CLASSES"]
    DATASET = config["DATASET"]
    LAMBDA = config["TRAINING"]["LAMBDA"]
    EPOCH_START = config["EPOCH_START"]
    EPOCH_END = config["EPOCH_END"]
    EPOCH_PERIOD = config["EPOCH_PERIOD"]
    SUBJECT_MODEL_NAME = config["TRAINING"]["SUBJECT_MODEL_NAME"]
    VIS_MODEL_NAME = config["VISUALIZATION"]["VIS_MODEL_NAME"]
    RESOLUTION = config["VISUALIZATION"]["RESOLUTION"]
    EPOCH = EPOCH_START + (iteration - 1)* EPOCH_PERIOD


    # define hyperparameters
    DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    import Model.model as subject_model
    try:
        net = eval("subject_model.{}()".format(SUBJECT_MODEL_NAME))
    except:
        raise NameError("No subject model found in model.py...")

    data_provider = DataProvider(CONTENT_PATH, net, EPOCH_START, EPOCH_END, EPOCH_PERIOD, split=-1, device=DEVICE, verbose=1)
    model = SingleVisualizationModel(input_dims=512, output_dims=2, units=256)
    negative_sample_rate = 5
    min_dist = .1
    # _a, _b = find_ab_params(1.0, min_dist)
    umap_loss_fn = UmapLoss(negative_sample_rate, DEVICE, _a=[1.0], _b=[1.0], repulsion_strength=1.0)
    recon_loss_fn = ReconstructionLoss(beta=1.0)
    criterion = SingleVisLoss(umap_loss_fn, recon_loss_fn, lambd=LAMBDA)

    optimizer = torch.optim.Adam(model.parameters(), lr=.01, weight_decay=1e-5)
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=4, gamma=.1)

    trainer = SingleVisTrainer(model, criterion=criterion, optimizer=optimizer, lr_scheduler=lr_scheduler, edge_loader=None, DEVICE=DEVICE)
    trainer.load(file_path=os.path.join(data_provider.model_path,"{}".format(VIS_MODEL_NAME)))
    trainer.model.eval()

    vis = visualizer(data_provider, trainer.model, RESOLUTION, 10, CLASSES)
    evaluator = Evaluator(data_provider, trainer)
    timevis = TimeVisBackend(data_provider, trainer, evaluator)


    train_data = data_provider.train_representation(EPOCH)
    test_data = data_provider.test_representation(EPOCH)
    all_data = np.concatenate((train_data, test_data), axis=0)

    embedding_2d = trainer.model.encoder(
        torch.from_numpy(all_data).to(dtype=torch.float32, device=trainer.DEVICE)).cpu().detach().numpy().tolist()

    train_labels = data_provider.train_labels(EPOCH)
    test_labels = data_provider.test_labels(EPOCH)
    labels = np.concatenate((train_labels, test_labels), axis=0).tolist()

    training_data_number = train_data.shape[0]
    testing_data_number = test_data.shape[0]
    testing_data_index = list(range(training_data_number, training_data_number + testing_data_number))

    grid, decision_view = vis.get_epoch_decision_view(EPOCH, RESOLUTION)

    grid = grid.reshape((-1, 2)).tolist()
    decision_view = decision_view * 255
    decision_view = decision_view.reshape((-1, 3)).astype(int).tolist()

    color = vis.get_standard_classes_color() * 255
    color = color.astype(int).tolist()

    # TODO fix its structure
    # evaluation = evaluator.get_eval(file_name="test_evaluation")
    eval_new = dict()
    # eval_new["nn_train_15"] = evaluation["15"]['nn_train'][str(EPOCH)]
    # eval_new['nn_test_15'] = evaluation["15"]['nn_test'][str(EPOCH)]
    # eval_new['bound_train_15'] = evaluation['15']['b_train'][str(EPOCH)]
    # eval_new['bound_test_15'] = evaluation['15']['b_test'][str(EPOCH)]
    # eval_new['ppr_train'] = evaluation['ppr_train'][str(EPOCH)]
    # eval_new['ppr_test'] = evaluation['ppr_test'][str(EPOCH)]
    #  eval_new = dict()
    eval_new["nn_train_15"] = 1
    eval_new['nn_test_15'] = 1
    eval_new['bound_train_15'] = 1
    eval_new['bound_test_15'] = 1
    eval_new['ppr_train'] = 1
    eval_new['ppr_test'] = 1

    label_color_list = []
    label_list = []
    for label in labels:
        label_color_list.append(color[int(label)])
        label_list.append(CLASSES[int(label)])

    prediction_list = []
    prediction = data_provider.get_pred(EPOCH, all_data).argmax(-1)

    for pred in prediction:
        prediction_list.append(CLASSES[pred])
    
    max_iter = (EPOCH_END - EPOCH_START) // EPOCH_PERIOD + 1

    _, conf_diff = timevis.batch_inv_preserve(EPOCH, all_data)
    current_index = timevis.get_epoch_index(EPOCH)

    new_index = timevis.get_new_index(iteration)

    noisy_data = timevis.noisy_data_index()

    original_labels = timevis.get_original_labels()
    original_label_list = []
    for label in original_labels:
        original_label_list.append(CLASSES[label])

    selected_points = np.arange(data_provider.train_num + data_provider.test_num)
    for key in predicates.keys():
        if key == "new_selection":
            tmp = np.array(timevis.get_new_index(int(predicates[key])))
        elif key == "label":
            tmp = np.array(timevis.filter_label(predicates[key]))
        elif key == "type":
            tmp = np.array(timevis.filter_type(predicates[key], int(iteration)))
        else:
            tmp = np.arange(data_provider.train_num + data_provider.test_num)
        selected_points = np.intersect1d(selected_points, tmp)

    sys.path.remove(CONTENT_PATH)


    return make_response(jsonify({'result': embedding_2d, 'grid_index': grid, 'grid_color': decision_view,
                                  'label_color_list': label_color_list, 'label_list': label_list,
                                  'maximum_iteration': max_iter, 'training_data': current_index,
                                  'testing_data': testing_data_index, 'evaluation': eval_new,
                                  'prediction_list': prediction_list,
                                  "selectedPoints":selected_points.tolist()}), 200)

# @app.route('/query', methods=["POST"])
# @cross_origin()
# def filter():
#     res = request.get_json()
#     if(res['predicates']):
#         label = res["predicates"]["label"]
#         conf = res["predicates"]["confidence"]


#     # CONTENT_PATH = os.path.normpath(res['content_path'])
#     # print(string,predicate)
#     # data =  InputStream(string)
#     # # lexer
#     # lexer = MyGrammarLexer(data)
#     # stream = CommonTokenStream(lexer)
#     # # parser
#     # parser = MyGrammarParser(stream)
#     # tree = parser.expr()
#     # # Currently this is hardcoded for CIFAR10, changes need to be made in future
#     # # Error will appear based on some of the queries sent
#     # model_epochs = [40, 80, 120, 160, 200]
#     # # evaluator
#     # listener = MyGrammarPrintListener(model_epochs)
#     # walker = ParseTreeWalker()
#     # walker.walk(listener, tree)
#     # statement = listener.result

#     # sql_engine       = create_engine('mysql+pymysql://xg:password@localhost/dviDB', pool_recycle=3600)
#     # db_connection    = sql_engine.connect()
#     # frame           = pd.read_sql(statement, db_connection);
#     # pd.set_option('display.expand_frame_repr', False)
#     # db_connection.close()
#     # result = []
#     # for _, row in frame.iterrows():
#     #     for col in frame.columns:
#     #         result.append(int(row[col]))
#     result = np.arange(200).tolist()
#     even = []
#     for i in range(len(result)):
#         if result[i]%2==0:
#           even.append(result[i])    #append增加对象到列表末尾
#     return make_response(jsonify({"selectedPoints":even}), 200)

@app.route('/query', methods=["POST"])
@cross_origin()
def filter():
    res = request.get_json()
    CONTENT_PATH = os.path.normpath(res['path'])
    EPOCH = int(res['iteration'])
    predicates = res["predicates"]

    sys.path.append(CONTENT_PATH)
    # timevis = initialize_backend(CONTENT_PATH)

    # training_data_number = timevis.hyperparameters["TRAINING"]["train_num"]
    # testing_data_number = timevis.hyperparameters["TRAINING"]["test_num"]

    # current_index = timevis.get_epoch_index(EPOCH)
    # selected_points = np.arange(training_data_number + testing_data_number)[current_index]
    # for key in predicates.keys():
    #     if key == "label":
    #         tmp = np.array(timevis.filter_label(predicates[key]))
    #     elif key == "type":
    #         tmp = np.array(timevis.filter_type(predicates[key], int(EPOCH)))
    #     else:
    #         tmp = np.arange(training_data_number + testing_data_number)
    #     selected_points = np.intersect1d(selected_points, tmp)
    # sys.path.remove(CONTENT_PATH)
    result = np.arange(200).tolist()
    even = []
    for i in range(len(result)):
        if result[i]%2==0:
          even.append(result[i])    #append增加对象到列表末尾
    return make_response(jsonify({"selectedPoints":even}), 200)

    # return make_response(jsonify({"selectedPoints": selected_points}), 200)

@app.route('/al_query', methods=["POST"])
@cross_origin()
def al_query():
    data = request.get_json()
    # CONTENT_PATH = os.path.normpath(data['content_path'])
    iteration = data["iteration"]
    strategy = data["strategy"]
    budget = int(data["budget"])
    print(iteration,strategy,budget)
    # sys.path.append(CONTENT_PATH)

    result = np.arange(budget).tolist()
    even = []
    for i in range(len(result)):
        if result[i]%2==0:
          even.append(result[i])    #append增加对象到列表末尾
    return make_response(jsonify({"selectedPoints":even}), 200)

@app.route('/al_train', methods=["POST"])
@cross_origin()
def al_train():
    res = request.get_json()
    CONTENT_PATH = os.path.normpath(res['content_path'])
    iteration = 3
    predicates = {}
    sys.path.append(CONTENT_PATH)

    # load hyperparameters
    config_file = os.path.join(CONTENT_PATH, "config.json")
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except:
        raise NameError("config file not exists...")

    CLASSES = config["CLASSES"]
    DATASET = config["DATASET"]
    LAMBDA = config["TRAINING"]["LAMBDA"]
    EPOCH_START = config["EPOCH_START"]
    EPOCH_END = config["EPOCH_END"]
    EPOCH_PERIOD = config["EPOCH_PERIOD"]
    SUBJECT_MODEL_NAME = config["TRAINING"]["SUBJECT_MODEL_NAME"]
    VIS_MODEL_NAME = config["VISUALIZATION"]["VIS_MODEL_NAME"]
    RESOLUTION = config["VISUALIZATION"]["RESOLUTION"]
    EPOCH = EPOCH_START + (iteration - 1)* EPOCH_PERIOD


    # define hyperparameters
    DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    import Model.model as subject_model
    try:
        net = eval("subject_model.{}()".format(SUBJECT_MODEL_NAME))
    except:
        raise NameError("No subject model found in model.py...")

    data_provider = DataProvider(CONTENT_PATH, net, EPOCH_START, EPOCH_END, EPOCH_PERIOD, split=-1, device=DEVICE, verbose=1)
    model = SingleVisualizationModel(input_dims=512, output_dims=2, units=256)
    negative_sample_rate = 5
    min_dist = .1
    # _a, _b = find_ab_params(1.0, min_dist)
    umap_loss_fn = UmapLoss(negative_sample_rate, DEVICE, _a=[1.0], _b=[1.0], repulsion_strength=1.0)
    recon_loss_fn = ReconstructionLoss(beta=1.0)
    criterion = SingleVisLoss(umap_loss_fn, recon_loss_fn, lambd=LAMBDA)

    optimizer = torch.optim.Adam(model.parameters(), lr=.01, weight_decay=1e-5)
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=4, gamma=.1)

    trainer = SingleVisTrainer(model, criterion=criterion, optimizer=optimizer, lr_scheduler=lr_scheduler, edge_loader=None, DEVICE=DEVICE)
    trainer.load(file_path=os.path.join(data_provider.model_path,"{}".format(VIS_MODEL_NAME)))
    trainer.model.eval()

    vis = visualizer(data_provider, trainer.model, RESOLUTION, 10, CLASSES)
    evaluator = Evaluator(data_provider, trainer)
    timevis = TimeVisBackend(data_provider, trainer, evaluator)


    train_data = data_provider.train_representation(EPOCH)
    test_data = data_provider.test_representation(EPOCH)
    all_data = np.concatenate((train_data, test_data), axis=0)

    embedding_2d = trainer.model.encoder(
        torch.from_numpy(all_data).to(dtype=torch.float32, device=trainer.DEVICE)).cpu().detach().numpy().tolist()

    train_labels = data_provider.train_labels(EPOCH)
    test_labels = data_provider.test_labels(EPOCH)
    labels = np.concatenate((train_labels, test_labels), axis=0).tolist()

    training_data_number = train_data.shape[0]
    testing_data_number = test_data.shape[0]
    testing_data_index = list(range(training_data_number, training_data_number + testing_data_number))

    grid, decision_view = vis.get_epoch_decision_view(EPOCH, RESOLUTION)

    grid = grid.reshape((-1, 2)).tolist()
    decision_view = decision_view * 255
    decision_view = decision_view.reshape((-1, 3)).astype(int).tolist()

    color = vis.get_standard_classes_color() * 255
    color = color.astype(int).tolist()

    # TODO fix its structure
    # evaluation = evaluator.get_eval(file_name="test_evaluation")
    eval_new = dict()
    # eval_new["nn_train_15"] = evaluation["15"]['nn_train'][str(EPOCH)]
    # eval_new['nn_test_15'] = evaluation["15"]['nn_test'][str(EPOCH)]
    # eval_new['bound_train_15'] = evaluation['15']['b_train'][str(EPOCH)]
    # eval_new['bound_test_15'] = evaluation['15']['b_test'][str(EPOCH)]
    # eval_new['ppr_train'] = evaluation['ppr_train'][str(EPOCH)]
    # eval_new['ppr_test'] = evaluation['ppr_test'][str(EPOCH)]
    #  eval_new = dict()
    eval_new["nn_train_15"] = 1
    eval_new['nn_test_15'] = 1
    eval_new['bound_train_15'] = 1
    eval_new['bound_test_15'] = 1
    eval_new['ppr_train'] = 1
    eval_new['ppr_test'] = 1

    label_color_list = []
    label_list = []
    for label in labels:
        label_color_list.append(color[int(label)])
        label_list.append(CLASSES[int(label)])

    prediction_list = []
    prediction = data_provider.get_pred(EPOCH, all_data).argmax(-1)

    for pred in prediction:
        prediction_list.append(CLASSES[pred])
    
    max_iter = (EPOCH_END - EPOCH_START) // EPOCH_PERIOD + 1

    current_index = timevis.get_epoch_index(EPOCH)

    new_index = timevis.get_new_index(iteration)

    # noisy_data = timevis.noisy_data_index()

    selected_points = np.arange(data_provider.train_num + data_provider.test_num)
    for key in predicates.keys():
        if key == "new_selection":
            tmp = np.array(timevis.get_new_index(int(predicates[key])))
        elif key == "label":
            tmp = np.array(timevis.filter_label(predicates[key]))
        elif key == "type":
            tmp = np.array(timevis.filter_type(predicates[key], int(iteration)))
        else:
            tmp = np.arange(data_provider.train_num + data_provider.test_num)
        selected_points = np.intersect1d(selected_points, tmp)

    sys.path.remove(CONTENT_PATH)


    return make_response(jsonify({'result': embedding_2d, 'grid_index': grid, 'grid_color': decision_view,
                                  'label_color_list': label_color_list, 'label_list': label_list,
                                  'maximum_iteration': max_iter, 'training_data': current_index,
                                  'testing_data': testing_data_index, 'evaluation': eval_new,
                                  'prediction_list': prediction_list,
                                  "selectedPoints":selected_points.tolist()}), 200)

@app.route('/saveDVIselections', methods=["POST"])
@cross_origin()
def save_DVI_selections():
    data = request.get_json()
    indices = data["newIndices"]

    CONTENT_PATH = os.path.normpath(data['content_path'])
    iteration = data["iteration"]
    sys.path.append(CONTENT_PATH)

    # load hyperparameters
    config_file = os.path.join(CONTENT_PATH, "config.json")
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except:
        raise FileNotFoundError("config file not exists...")


    LAMBDA = config["TRAINING"]["LAMBDA"]
    EPOCH_START = config["EPOCH_START"]
    EPOCH_END = config["EPOCH_END"]
    EPOCH_PERIOD = config["EPOCH_PERIOD"]
    SUBJECT_MODEL_NAME = config["TRAINING"]["SUBJECT_MODEL_NAME"]
    VIS_MODEL_NAME = config["VISUALIZATION"]["VIS_MODEL_NAME"]
    EPOCH = EPOCH_START + (iteration - 1)* EPOCH_PERIOD


    # define hyperparameters
    DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    import Model.model as subject_model
    try:
        net = eval("subject_model.{}()".format(SUBJECT_MODEL_NAME))
    except:
        raise NameError("No subject model found in model.py...")

    data_provider = DataProvider(CONTENT_PATH, net, EPOCH_START, EPOCH_END, EPOCH_PERIOD, split=-1, device=DEVICE, verbose=1)
    model = SingleVisualizationModel.SingleVisualizationModel(input_dims=512, output_dims=2, units=256)
    negative_sample_rate = 5
    min_dist = .1
    # _a, _b = find_ab_params(1.0, min_dist)
    umap_loss_fn = UmapLoss(negative_sample_rate, DEVICE, _a=[1.0], _b=[1.0], repulsion_strength=1.0)
    recon_loss_fn = ReconstructionLoss(beta=1.0)
    criterion = SingleVisLoss(umap_loss_fn, recon_loss_fn, lambd=LAMBDA)

    optimizer = torch.optim.Adam(model.parameters(), lr=.01, weight_decay=1e-5)
    lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=4, gamma=.1)

    trainer = SingleVisTrainer(model, criterion=criterion, optimizer=optimizer, lr_scheduler=lr_scheduler, edge_loader=None, DEVICE=DEVICE)
    trainer.load(file_path=os.path.join(data_provider.model_path,"{}".format(VIS_MODEL_NAME)))
    trainer.model.eval()

    evaluator = Evaluator(data_provider, trainer)
    timevis = TimeVisBackend(data_provider, trainer.model, evaluator)

    timevis.save_DVI_selection(EPOCH, indices)
    sys.path.remove(CONTENT_PATH)

    return make_response(jsonify({"message":"Save DVI selection succefully!"}), 200)


def image_cut_save(path, left, upper, right, lower, save_path):
    """
        所截区域图片保存
    :param path: 图片路径
    :param left: 区块左上角位置的像素点离图片左边界的距离
    :param upper：区块左上角位置的像素点离图片上边界的距离
    :param right：区块右下角位置的像素点离图片左边界的距离
    :param lower：区块右下角位置的像素点离图片上边界的距离
     故需满足：lower > upper、right > left
    :param save_path: 所截图片保存位置
    """
    img = Image.open(path)  # 打开图像
    box = (left, upper, right, lower)
    roi = img.crop(box)
    # print('img_stream',img_stream)
    # 保存截取的图片
    roi.save(save_path)
    # readImg(save_path)

# @app.route('/sprite', methods=["POST","GET"])
# @cross_origin()
# def sprite_image():
#     index=request.args.get("index")
#     print('index',index)
#     i = int(index)

#     pic_path = '/Users/zhangyifan/Downloads/toy_model/resnet18_cifar10/cifar10.png'
#     pic_save_dir_path = '/Users/zhangyifan/Downloads/toy_model/resnet18_cifar10/img/new.png'
#     left, upper, right, lower = 0, 0, 32, 32
#     left =  (i%245)*32
#     upper = round(i/245)*32
#     right = left+32
#     lower = upper+32
#     name = "img" + str(i)
#     pic_save_dir_path = '/Users/zhangyifan/Downloads/toy_model/resnet18_cifar10/img/'+name+'.png'
#     print(left,upper,right,lower,name,pic_save_dir_path)
#     image_cut_save(pic_path, left, upper, right, lower, pic_save_dir_path)
#     img_stream = ''
#     with open(pic_save_dir_path, 'rb') as img_f:
#         img_stream = img_f.read()
#         img_stream = base64.b64encode(img_stream).decode()
#     image_type = "image/png"
#     # print('img_stream',img_stream)
#     return make_response(jsonify({"imgUrl":img_stream}), 200)

@app.route('/sprite', methods=["POST","GET"])
@cross_origin()
def sprite_image():
    path = request.args.get("path")
    index=request.args.get("index")

    CONTENT_PATH = os.path.normpath(path)
    print('index', index)
    idx = int(index)
    pic_save_dir_path = os.path.join(CONTENT_PATH, "sprites", "{}.png".format(idx))
    img_stream = ''
    with open(pic_save_dir_path, 'rb') as img_f:
        img_stream = img_f.read()
        img_stream = base64.b64encode(img_stream).decode()
    image_type = "image/png"
    return make_response(jsonify({"imgUrl":img_stream}), 200)

@app.route('/json', methods=["POST","GET"])
@cross_origin()
def sprite_json():
    with open('graphic.json', 'r') as f:
       config = json.load(f)
    return make_response(jsonify({"imgUrl":config}), 200)
# if this is the main thread of execution first load the model and then start the server
if __name__ == "__main__":
    with open('config.json', 'r') as f:
        config = json.load(f)
        ip_adress = config["DVIServerIP"]
        port = config["DVIServerPort"]
    app.run(host=ip_adress, port=int(port))
