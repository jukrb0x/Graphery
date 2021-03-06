# Generated by Django 3.0.8 on 2020-08-18 22:22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0015_added_level_mixin'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='graph',
            options={'ordering': ['-priority']},
        ),
        migrations.AlterModelOptions(
            name='tutorial',
            options={'ordering': ['level', 'section']},
        ),
        migrations.AlterField(
            model_name='tutorial',
            name='level',
            field=models.PositiveSmallIntegerField(),
        ),
        migrations.AlterField(
            model_name='tutorial',
            name='section',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
