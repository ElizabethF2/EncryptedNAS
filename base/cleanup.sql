/* Orphaned Tags */
/* SELECT Tags.Name FROM Tags WHERE NOT EXISTS (SELECT * FROM File_Tags WHERE File_Tags.Tag = Tags.ID) */
/* DELETE FROM Tags WHERE NOT EXISTS (SELECT * FROM File_Tags WHERE File_Tags.Tag = Tags.ID) */

/* Orphaned File_Tags */
/* SELECT * FROM File_Tags WHERE NOT EXISTS (SELECT * FROM Tags WHERE File_Tags.Tag = Tags.ID) OR NOT EXISTS (SELECT * FROM Files WHERE File_Tags.File = Files.ID) */

/* Orphaned Files */
SELECT * FROM Files WHERE NOT EXISTS (SELECT * FROM File_Tags WHERE File_Tags.File = Files.ID)
