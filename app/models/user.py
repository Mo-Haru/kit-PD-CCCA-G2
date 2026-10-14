"""ルートの models.py から移した下書き。

クラスはコメントのままなので、テーブルは作られない。
有効にするときは app.extensions の db を使い、ここだけで SQLAlchemy() を新しく作らない。
"""

# from flask_sqlalchemy import SQLAlchemy
#
# db = SQLAlchemy()
#
# class User(db.Model):
#     id = db.Column(db.Integer, primary_key=True)
