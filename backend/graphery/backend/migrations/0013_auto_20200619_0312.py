# Generated by Django 3.0.7 on 2020-06-19 03:12

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0012_auto_20200618_2325'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tutorial',
            name='url',
            field=models.CharField(default=None, max_length=50, unique=True),
        ),
        migrations.AddIndex(
            model_name='category',
            index=models.Index(fields=['category'], name='backend_cat_categor_cfa311_idx'),
        ),
    ]
