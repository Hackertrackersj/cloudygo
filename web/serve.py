#!/usr/bin/python
#
# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import itertools
import math
import os
import random
import re
import sqlite3
import time

from collections import defaultdict
from datetime import datetime

from werkzeug.contrib.cache import SimpleCache
from flask import Flask, g
from flask import request, render_template
from flask import send_file, url_for, redirect, jsonify

from . import sgf_utils
from .cloudygo import CloudyGo

app = Flask(__name__)
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True

cache = SimpleCache()

LOCAL_DATA_DIR = os.path.join(app.instance_path, 'data')
DATABASE_PATH = os.path.join(app.instance_path, 'clouds.db')

RANDOMIZE_GAMES = True
MAX_GAMES_ON_PAGE = 100
MAX_INSERTS = 5000

#### DB STUFF ####

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


print('Setting up Cloudy')
cloudy = CloudyGo(
    app.instance_path,
    LOCAL_DATA_DIR,
    get_db,
    cache,
    None, # multiprocessing pool
)

#### UTILS ####

def get_bool_arg(name, args):
    value = args.get(name, 'false').lower()
    return value not in ('f', 'false')

@app.template_filter('strftime')
def _jinja2_filter_strftime(time, fmt=None):
    date = datetime.fromtimestamp(int(time))
    tformat='%Y-%m-%d %H:%M'
    return date.strftime(tformat)

#### PAGES ####

@app.route('/213-secret-site-nav')
@app.route('/<bucket>/213-secret-site-nav')
def debug(bucket=CloudyGo.DEFAULT_BUCKET):
    db_mtime = os.path.getmtime(DATABASE_PATH)
    secret_vars = [
        'app.instance_path: ' + app.instance_path,
        'DEFAULT_BUCKET: ' + cloudy.DEFAULT_BUCKET,
        '[UNUSED] app.root_path: ' + app.root_path,
        'DATABASE_FILE: {}'.format(os.path.exists(DATABASE_PATH)),
        'DATABASE_RAW: {}'.format(os.stat(DATABASE_PATH).st_size),
        'DATABASE M_TIME: {}'.format(datetime.fromtimestamp(db_mtime)),
    ]

    # try to filter some of the rsync lines
    patterns = list(map(re.compile, [
        r'.*\[[0-9.k]*/[0-9.]*k files\]\[.*Done',
        r'.*\[[3-9][0-9]{2}/.* files\]\[.*Done',
        r'Copying gs://.*/sgf/.*sgf',
        r'[0-9]{3,}it ',
        r'^.{0,3}$',
    ]))

    def not_boring_line(line):
        return not any(pattern.match(line) for pattern in patterns)


    log_files = ['cloudy-rsync-cron.log', 'cloudy-eval.log']
    log_datas = []
    for log in log_files:
        log_filename = os.path.join(app.instance_path, 'debug', log)
        if not os.path.exists(log_filename):
            print ("log: {} does not exist".format(log_filename))
            continue

        log_data = ''
        with open(log_filename, 'r', encoding='utf-8') as log_file:
            log_data = log_file.readlines()
        log_data = list(filter(not_boring_line, log_data))

        log_datas.append((log_filename, log_data))

    return render_template(
        'secret-site-nav.html',
        logs        = log_datas,
        secret_vars = secret_vars,
    )


@app.route('/openings/<filename>')
def opening_image(filename):
    return send_file(
        os.path.join(app.instance_path, 'openings', filename),
        mimetype='image/png',
        cache_timeout=60*60)


@app.route('/eval/<bucket>/<filename>')
def eval_image(bucket, filename):
    return send_file(
        os.path.join(app.instance_path, 'eval', bucket, filename),
        mimetype='image/png',
        cache_timeout=30*60)


@app.route('/eval/<bucket>/selfplay/<filename>')
def selfplay_eval_image(bucket, filename):
    return send_file(
        os.path.join(app.instance_path, 'eval', bucket, 'selfplay', filename),
        mimetype='image/png',
        cache_timeout=30*60)


@app.route('/<bucket>/<model>/game/<filename>')
def game_view(bucket, model, filename):
    view_type = request.args.get('type') or 'clean'
    is_raw = get_bool_arg('raw', request.args)

    data, game_view = cloudy.get_game_data(
        bucket, model, filename, view_type)

    if is_raw:
        return sgf_utils.pretty_print_sgf(data)

    render_sorry = game_view != view_type

    return render_template('game.html',
        bucket = bucket,
        model  = model,
        data   = data,
        filename = filename,
        render_sorry = render_sorry,
    )


@app.route('/secret-pro-games/<path:filename>')
def pro_game_view(filename):
    base_dir = os.path.join(app.instance_path, 'pro')
    file_path = os.path.join(base_dir, filename)

    # make sure filename is in pro directory
    base_dir_abs = os.path.abspath(base_dir)
    file_path_abs = os.path.abspath(file_path)

    if not file_path_abs.startswith(base_dir_abs):
        return 'being naughty?'

    data = ''
    with open(file_path_abs, 'r') as f:
        data = f.read()

    return render_template('game.html',
        bucket = CloudyGo.DEFAULT_BUCKET, #Any value will do
        model  = '100',      # needs some value
        data   = data,
        filename = filename,
    )


@app.route('/<bucket>/figure-three')
def figure_three(bucket):
    exists = os.path.exists(os.path.join(
        app.instance_path, 'eval', bucket, 'move_acc.png'))


    return render_template('figure-three.html',
        bucket      = bucket,
        exists      = exists,
        eval_files  = [
            'move_acc.png',  'value_mse.png',
            'move_acc2.png', 'value_mse2.png',
            'selfplay/move_acc.png',  'selfplay/value_mse.png',
            'selfplay/move_acc2.png', 'selfplay/value_mse2.png'],
    )


@app.route('/<bucket>/missing-games/<model_id>')
def missing_games(bucket, model_id):
    missing = cloudy.bucket_query_db(
        bucket,
        'SELECT filename',
        'games',
        'WHERE (model_id % 10000) = ? AND '
        '    game_num NOT IN (SELECT game_num FROM game_stats) ',
        1,
        args=(int(model_id),))
    return 'Missing games for {} ({}): {}'.format(
        model_id, len(missing), '<br>'.join(m[0] for m in missing))


@app.route('/<bucket>/force-update-models')
def update_models(bucket):
    return cloudy.update_models(bucket)


@app.route('/<bucket>/force-update-games')
def update_games(bucket):
    stats = get_bool_arg('stats', request.args)
    max_inserts = int(request.args.get('n', MAX_INSERTS))
    min_model = int(request.args.get('min_model', 0))

    count, status = cloudy.update_games(bucket, stats, max_inserts, min_model)
    return status


@app.route('/site-nav')
@app.route('/<bucket>/site-nav')
def site_nav(bucket=CloudyGo.DEFAULT_BUCKET):
    model_name = cloudy.get_newest_model_num(bucket)

    return render_template(
        'site-nav.html',
        bucket=bucket,
        model=model_name)


@app.route('/')
@app.route('/<bucket>/models/')
def models_details(bucket=CloudyGo.DEFAULT_BUCKET):
    # Limit to top ~120 interesting models
    models = sorted(cloudy.get_models(bucket))[::-1]

    if len(models) > 120:
        trim_count = len(models) - 120
        skippable = sorted(m[0] for m in models if m[0] % 10 != 0)
        to_skip = set(skippable[:trim_count])
        models = [m for m in models if m[0] not in to_skip]

    models = [list(m) for m in models]

    last_update=max((m[5] for m in models), default=0)
    for m in models:
        # creation timestamp
        m[6] = CloudyGo.time_stamp_age(m[6])

    return render_template(
        'models.html',
        bucket=bucket,
        models=models,
        last_update=last_update,
    )


@app.route('/<bucket>/graphs')
def models_graphs(bucket):
    model_limit = int(request.args.get('last_n', 500))
    model_range = CloudyGo.bucket_model_range(bucket)

    key = '{}/graphs/{}'.format(bucket, model_limit)
    graphs = cache.get(key)
    if graphs is None:
        win_rate = cloudy.bucket_query_db(
            bucket,
            'SELECT model_id % 10000, round(1.0*wins/stats_games,3)',
            'model_stats', 'WHERE perspective = "black"', 1, model_limit)

        bad_resign_rate = cloudy.bucket_query_db(
            bucket,
            'SELECT model_id % 10000, round(1.0*bad_resigns/hold_out_resigns,3)',
            'model_stats',
            'WHERE hold_out_resigns > 0 and perspective = "all" ',
            1,
            model_limit)

        game_length_simple = cloudy.bucket_query_db(
            bucket,
            'SELECT model_id % 10000, round(1.0*num_moves/stats_games,3)',
            'model_stats', 'WHERE perspective = "all"', 1, model_limit)
            #'SELECT model_id % 10000, round(1.0*sum(num_moves)/count(*),3)',
            #'games', '', 1, model_limit)

        num_games = cloudy.bucket_query_db(
            bucket,
            'SELECT model_id % 10000, num_games, stats_games',
            'model_stats', 'WHERE perspective = "all"', 1, model_limit)

        games_per_day = cloudy.bucket_query_db(
            bucket,
            'SELECT date(creation, "unixepoch"), sum(num_games)',
            'models', '', 1)

        num_visits = cloudy.bucket_query_db(
            bucket,
            'SELECT model_id % 10000, number_of_visits/stats_games',
            'model_stats', 'WHERE perspective = "all"', 1, model_limit)

        sum_unluck = cloudy.bucket_query_db(
            bucket,
            'SELECT model_id % 10000, round(sum_unluckiness/stats_games,2)',
            'model_stats', 'WHERE perspective = "all"', 1, model_limit)

        rating_delta = cloudy.query_db(
            'SELECT m.model_id_1 % 10000, m.rankings - m2.rankings '
            'FROM eval_models m INNER JOIN eval_models m2 '
            'WHERE m.model_id_2 = 0 AND m2.model_id_2 = 0 '
            '   AND m2.model_id_1 = m.model_id_1 - 1 '
            '   AND m.model_id_1 >= ? AND m.model_id_1 < ? '
            'ORDER BY m.model_id_1 desc LIMIT ?',
            model_range + (model_limit,))
        rating_delta = list(reversed(rating_delta))

        graphs = (win_rate, bad_resign_rate,
            game_length_simple,
            num_games, games_per_day,
            num_visits,
            rating_delta,
            sum_unluck)
        cache.set(key, graphs, timeout = 10 * 60)
    else:
        win_rate, bad_resign_rate, \
        game_length_simple, \
        num_games, games_per_day, \
        num_visits, \
        rating_delta, \
        sum_unluck = graphs

    return render_template('models-graphs.html',
        bucket      = bucket,
        win_rate    = win_rate,
        bad_resign_rate = bad_resign_rate,
        game_len_simple = game_length_simple,
        num_games   = num_games,
        games_per_day = games_per_day,
        num_visits  = num_visits,
        rating_delta = rating_delta,
        sum_unluck  = sum_unluck,
    )


@app.route('/<bucket>/graphs-sliders')
def models_graphs_sliders(bucket):
    key = '{}/graphs-sliders'.format(bucket)
    graphs = cache.get(key)
    if graphs is None:
        # Divide by four to help avoid the 'only black can win on even moves'
        game_length = cloudy.bucket_query_db(
            bucket,
            'SELECT model_id % 10000, black_won, 4*(num_moves/4), count(*)',
            'games', 'WHERE model_id % 10000 >= 50 ', 3, limit=20000)

        sum_unluck_per = cloudy.bucket_query_db(
            bucket,
            'SELECT '
            '   model_id % 10000, black_won, '
            '   round(100 * (unluckiness_black - unluckiness_white) / '
            '       (unluckiness_black + unluckiness_white), 0), '
            '   count(*) ',
            'game_stats', '', 3, limit=20000)

        picture_sliders = []

        # TODO replace with list of SGFs
        models = sorted(cloudy.get_models(bucket))
        for model in models:
            model_id = str(model[0])
            opening  = model_id+'-favorite-openings.png'
            policy   = model_id+'-policy-empty.png'

            picture_sliders.append((
                model[0] % CloudyGo.SALT_MULT,
                url_for('.opening_image', filename=opening),
                url_for('.opening_image', filename=policy)
            ))


        graphs = (game_length, sum_unluck_per, picture_sliders)
        cache.set(key, graphs, timeout = 10 * 60)
    else:
        game_length, sum_unluck_per, picture_sliders = graphs

    return render_template('models-graphs-sliders.html',
        bucket          = bucket,
        game_length     = game_length,
        sum_unluck_per  = sum_unluck_per,
        picture_sliders = picture_sliders,
    )


@app.route('/<bucket>/model_comparison/policy/<model_name_a>/<model_name_b>')
@app.route('/<bucket>/model_comparison/pv/<model_name_a>/<model_name_b>')
def position_comparison(bucket, model_name_a, model_name_b):
    model_a, _ = cloudy.load_model(bucket, model_name_a)
    model_b, _ = cloudy.load_model(bucket, model_name_b)
    if model_a is None or model_b is None:
        return 'Model {} or {} not found'.format(model_name_a, model_name_b)

    rule_group = ('policy' if '/policy/' in request.url_rule.rule else 'pv')
    arg_group = request.args.get('group', None)
    group = arg_group or rule_group

    count, data = cloudy.get_position_sgfs(bucket, [model_a[0], model_b[0]])

    return render_template('position-comparison.html',
        bucket   = bucket,
        model_a  = model_a,
        model_b  = model_b,
        group    = group,
        sgfs     = data,
    )


@app.route('/<bucket>/models_evolution/')
def models_evolution(bucket):
    count, sgfs = cloudy.get_position_sgfs(bucket)

    return render_template('position-evolution.html',
        bucket   = bucket,
        sgfs     = sgfs,
        count    = count,
    )


@app.route('/<bucket>/eval-graphs')
def eval_graphs(bucket):
    model_range = CloudyGo.bucket_model_range(bucket)
    bucket_salt = CloudyGo.bucket_salt(bucket)

    eval_models = cloudy.query_db(
        'SELECT * FROM eval_models '
        'WHERE model_id_1 >= ? and model_id_1 < ? and '
        '   model_id_2 = 0 and games >= 10 ',
        model_range)
    # Drop model_id_2 and subtract bucket_salt
    eval_models = [(d[0] - bucket_salt,) + d[2:] for d in eval_models]

    total_games = sum(e_m[4] for e_m in eval_models)

    if len(eval_models) < 5:
        return render_template('models-eval-empty.html',
            bucket = bucket, total_games = total_games)

    older_newer_winrates = cloudy.query_db(
        'SELECT model_id_1 % 10000, '
        '       sum((model_id_1 > model_id_2) * (m1_black_wins+m1_white_wins)), '
        '       sum((model_id_1 > model_id_2) * games), '
        '       sum((model_id_1 < model_id_2) * (m1_black_wins+m1_white_wins)), '
        '       sum((model_id_1 < model_id_2) * games) '
        'FROM eval_models '
        'WHERE model_id_1 >= ? and model_id_1 < ? and model_id_2 != 0 '
        'GROUP BY 1 ORDER BY 1 asc',
        model_range)

    top_ten_threshold = 0.5
    if len(eval_models) > 10:
        top_ten_threshold = sorted([e_m[1] for e_m in eval_models])[-10]

    worst_model = min(eval_models, key=lambda p: p[1], default=(0, 0))
    best_model = max(eval_models, key=lambda p: p[1], default=(0, 0))

    return render_template('models-eval.html',
        bucket           = bucket,
        model_ratings    = sorted(eval_models),
        older_newer_winrates = older_newer_winrates,
        total_games      = total_games,
        best_model       = best_model,
        worst_model      = worst_model,
        great_threshold  = top_ten_threshold
    )


@app.route('/<bucket>/eval-model/<model_name>')
def model_eval(bucket, model_name):
    model, model_stats = cloudy.load_model(bucket, model_name)

    eval_models = cloudy.query_db(
        'SELECT * FROM eval_models '
        'WHERE model_id_1 = ?',
        (model[0],))
    total_games = sum(e_m[2] for e_m in eval_models)

    if total_games == 0:
        return 'No games for ' + model_name

    overall = [e_m for e_m in eval_models if e_m[1] == 0][0]
    eval_models.remove(overall)
    overall = list(overall)
    overall[0] %= CloudyGo.SALT_MULT

    rating = overall[2]

    updated = []
    played_better = 0
    later_models = [0, 0]
    earlier_models = [0, 0]
    for e_m in eval_models:
        # Make models more familiar
        cur_id = e_m[0] % CloudyGo.SALT_MULT
        other_id = e_m[1] % CloudyGo.SALT_MULT
        rating_diff = 2 * (e_m[2] - rating)
        winrate = 100 / (1 + 10 ** (rating_diff / 400))

        updated.append((cur_id, other_id, winrate) + e_m[3:])

        # e_m[2] is average rating (of ours + theirs)
        if e_m[2] > rating:
            played_better += 1

        if e_m[1] < e_m[0]:
            earlier_models[0] += e_m[4]
            earlier_models[1] += e_m[6] + e_m[8]
        else:
            later_models[0] += e_m[4]
            later_models[1] += e_m[6] + e_m[8]

    eval_games = cloudy.query_db(
        'SELECT model_id_1 % 10000, model_id_2 % 10000, filename '
        'FROM eval_games '
        'WHERE model_id_1 = ? or model_id_2 = ?',
        (model[0],model[0]))

    return render_template('model-eval.html',
        bucket         = bucket,
        total_games    = total_games,
        overall        = overall,
        played_better  = played_better,
        later_models   = later_models,
        earlier_models = earlier_models,
        model_games    = updated,
        eval_games     = eval_games,
    )


# Supports full name (0000102-monarch as well as 102)
@app.route('/<bucket>/details/<model_name>')
def model_details(bucket, model_name):
    if model_name == 'newest':
        model_name = cloudy.get_newest_model_num(bucket)

    model, model_stats = cloudy.load_model(bucket, model_name)
    if model is None:
        return 'Model {} not found'.format(model_name)
    model_id = model[0]
    model_name = model[2]
    model_num = model[4]

    games = cache.get(model_name)
    if games is None:
        games = cloudy.all_games(bucket, model_name)
        games = [os.path.basename(game) for game in games]

        if RANDOMIZE_GAMES:
            random.shuffle(games)

        games = games[:MAX_GAMES_ON_PAGE]

        # Low cache time so that games randomize if you refresh
        cache.set(model_name, games, timeout=60)

    details, game_stats = cloudy.load_games(bucket, games)

    #### MIN UNLUCK ####
    unluck_by = [
        ('black', 'unluckiness_black'),
        ('white', 'unluckiness_white'),
        ('black+white', 'unluckiness_black + unluckiness_white'),
    ]

    min_unluck = []
    for perspective, order_by in unluck_by:
        min_unluck_game = cloudy.query_db(
            'SELECT filename, {} FROM game_stats '
            'INNER JOIN games on game_stats.game_num = games.game_num '
            'WHERE game_stats.model_id = ? AND games.num_moves > 70 '
            'ORDER BY 2 ASC LIMIT 1'.format(order_by),
            (model_id,))
        if min_unluck_game:
            min_unluck.append((perspective,) + min_unluck_game[0])

    opening_sgf = ''
    if model_stats is not None:
        opening_sgf = model_stats[0][14]

    policy_sgf = cloudy.get_position_eval(bucket, model_id, 'policy', 'empty')

    return render_template('model.html',
        bucket       = bucket,
        model        = model, model_stats=model_stats,
        prev_model   = model_num-1,
        next_model   = model_num+1,
        games        = details,
        game_stats   = game_stats,
        min_unluck   = min_unluck,
        is_random    = RANDOMIZE_GAMES,
        opening_sgf  = opening_sgf,
        policy_sgf   = policy_sgf,
    )


@app.route('/<bucket>/graphs/<model_name>')
def model_graphs(bucket, model_name):
    # TODO(sethtroisi): cache something here

    model, model_stats = cloudy.load_model(bucket, model_name)
    if model is None:
        return 'Model {} not found'.format(model_name)
    model_id = model[0]

    # Divide by two to help avoid the 'only black can win on even moves'
    game_length = cloudy.query_db(
        'SELECT black_won, 2*(num_moves/2), count(*) FROM games ' +
        'WHERE model_id = ? GROUP BY 1,2 ORDER BY 1,2', (model[0],))

    #### OPENING RESPONSES ####

    favorite_openings = cloudy.query_db(
        'SELECT SUBSTR(early_moves_canonical,'
        '              0, instr(early_moves_canonical, ";")),'
        '       count(*)'
        'FROM game_stats WHERE model_id = ? GROUP BY 1 ORDER BY 2 DESC LIMIT 16',
        (model_id,))

    favorite_response = []
    for black_first_move, opening_count in favorite_openings:
        # Pass is 5 long and messes up the indexeing on 5+...
        len_move = len(black_first_move) + 2
        response = cloudy.query_db(
            'SELECT SUBSTR(early_moves_canonical, '
            '              0, ?+instr(SUBSTR(early_moves_canonical, ?, 6), ";")),'
            '       count(*) '
            'FROM game_stats '
            'WHERE model_id = ? AND num_moves > 2 AND early_moves_canonical LIKE ?'
            'GROUP BY 1 ORDER BY 2 DESC LIMIT 8',
            (len_move, len_move, model_id, black_first_move + ';%'))

        # Trim first move
        board_size = CloudyGo.bucket_to_board_size(bucket)

        response = [(moves.split(';')[1], count) for moves, count in response]
        favorite_response.append((
            black_first_move,
            opening_count,
            sum(count for move, count in response),
            sgf_utils.commented_squares(
                board_size,
                ';B[{}]'.format(sgf_utils.cord_to_sgf(
                    board_size, black_first_move)),
                response, True, False)))

    #### SOME POLICY EVALS ####

    count, sgfs = cloudy.get_position_sgfs(bucket, [model_id])
    if sgfs:
        sgfs = sgfs[0][1:]

    return render_template('model-graphs.html',
        bucket      = bucket,
        model       = model,
        model_stats = model_stats,
        game_length = game_length,
        opening_responses = favorite_response,
        position_sgfs = sgfs,
    )


@app.route('/<bucket>/json/missing-ratings.json')
def ratings_json(bucket):
    # Not used by CloudyGo but easy to support for external people

    model_range = CloudyGo.bucket_model_range(bucket)
    models = cloudy.get_newest_model_num(bucket)

    pairs = defaultdict(lambda : defaultdict(int))

    data = cloudy.query_db(
        'SELECT model_id_1 % 10000, model_id_2 % 10000, games '
        'FROM eval_models '
        'WHERE model_id_1 < model_id_2 AND'
        '      model_id_1 >= ? and model_id_1 < ?',
        model_range)

    for m_1, m_2, g in data:
        assert m_1 < m_2
        pairs[m_1][m_2] += g
        pairs[m_2][m_1] += g

    delta = list(itertools.chain(
        range(1, 10),
        range(10, 20, 2),
        range(20, 51, 5),
    ))

    required = 6

    missing = []
    for m in range(models):
        for d in delta:
            if m < d:
                continue

            count = pairs[m][m - d]
            if count < required:
                missing.append((m, m-d, count))

    return str(missing)


@app.route('/<bucket>/json/eval-pairs.json')
def eval_json(bucket):
    # Not used by CloudyGo but easy to support for external people

    model_range = CloudyGo.bucket_model_range(bucket)
    models = cloudy.get_newest_model_num(bucket)

    data = cloudy.query_db(
        'SELECT '
        '   model_id_1 % 10000, model_id_2 % 10000, '
        '   m1_black_wins + m1_white_wins, games '
        'FROM eval_models '
        'WHERE model_id_1 < model_id_2 AND'
        '      model_id_1 >= ? and model_id_1 < ?',
        model_range)
    return str(data)


@app.route('/<bucket>/json/ratings.json')
def ratings(bucket):
    # Not used by CloudyGo but easy to support for external people

    model_range = CloudyGo.bucket_model_range(bucket)
    models = cloudy.get_newest_model_num(bucket)

    ratings = cloudy.query_db(
        'SELECT model_id_1 % 10000, round(rankings, 3), round(std_err,3) '
        'FROM eval_models '
        'WHERE model_id_2 == 0 AND '
        '      model_id_1 >= ? AND model_id_1 < ?',
        model_range)
    return str(ratings)

